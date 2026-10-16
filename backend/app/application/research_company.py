"""Research the company and the founder before the letter is written
(operator request 2026-09-25).

His pipeline document, step by step:

1. **Research the company.** What they do in their own words, one
   specific named thing (a product, a model, a feature, a technical
   choice, a blog post, a repo or dataset), one real fact about the
   founder, and one technical question an engineer would genuinely ask.
2. **Verify every fact.** Only facts found in a source are passed on,
   each with its URL. A fact from something two or more years old is
   marked OLD, so the letter says "I read that ..." rather than "you
   do ...".
3. The writer (`Drafter`) is handed these facts and nothing else.
4. The operator is shown the facts with their URLs next to the letter.

`gather_sources` builds the numbered source list: the YC page, their
launch posts and news (already in the database), the founder's YC bio,
and - when a `WebSearch` is configured - what a web search found about
the company and the founder, cached so each is searched once.
`run_researcher` asks a `CompanyResearcher` to pick the facts, then
checks each one against the source it cites and drops what does not hold
up. Nothing here raises: research that fails means the letter is written
from the YC description alone, and the review screen says so.

Pure application logic - ports only, never `app.adapters`.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from app.application.write_founder_line import founder_web_results
from app.domain.names import company_display_name, company_name_variants

log = logging.getLogger(__name__)

OLD_AFTER = timedelta(days=730)   # "2+ years" in his document
MAX_COMPANY_RESULTS = 6
MAX_SOURCE_CHARS = 1500
FACT_KINDS = ("what_they_do", "specific_thing", "founder_fact")

_STOP = frozenset("""
a an and are as at be been but by for from has have in into is it its of on or
that the their them they this to was we were which who with you your our also
using use uses used than more most all can not no new one two its it's
""".split())


def _yc_url(company) -> str:
    slug = (getattr(company, "slug", None) or "").strip()
    return f"https://www.ycombinator.com/companies/{slug}" if slug else ""


def _date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def company_web_results(company, search=None, company_repo=None,
                        now: datetime | None = None) -> list[dict]:
    """What the web says about THIS company - cached on the company, so
    it is searched at most once. Only results that name the company are
    kept: a search for a short name finds everything else called that.
    A failed search is not cached, so it is tried again next time."""
    cached = getattr(company, "web_research", None)
    if getattr(company, "web_researched_at", None) is not None:
        return list(cached or [])
    if search is None:
        return []
    name = company_display_name(getattr(company, "name", "") or "")
    one_liner = (getattr(company, "one_liner", None) or "").strip()
    queries = [
        f'"{name}" {one_liner}'.strip(),
        f'"{name}" engineering blog OR github OR huggingface OR model OR launch',
    ]
    variants = [v.casefold() for v in company_name_variants(name)] or [name.casefold()]
    kept, seen = [], set()
    try:
        for query in queries:
            for r in search.search(query, max_results=6):
                url = (r.get("url") or "").strip()
                text = f"{r.get('title', '')}\n{r.get('content', '')}".casefold()
                if not url or url in seen or not any(v in text for v in variants):
                    continue
                seen.add(url)
                kept.append({k: str(r.get(k) or "") for k in ("title", "url", "content", "published_date")})
    except Exception as exc:  # noqa: BLE001 - a failed search is no material
        log.warning("research: company web search failed (%s)", type(exc).__name__)
        return kept[:MAX_COMPANY_RESULTS]
    kept = kept[:MAX_COMPANY_RESULTS]
    if company_repo is not None:
        company_repo.set_web_research(company.id, kept, now or datetime.now(timezone.utc))
    return kept


def gather_sources(company, founder, news=(), launches=(), search=None,
                   company_repo=None, founder_repo=None, now: datetime | None = None) -> list[dict]:
    """Every source the facts may come from, numbered S1, S2, ...:
    `{id, label, text, url, date}`."""
    yc = _yc_url(company)
    raw: list[dict] = []
    desc = (getattr(company, "long_description", None) or "").strip()
    one_liner = (getattr(company, "one_liner", None) or "").strip()
    if desc or one_liner:
        raw.append({"label": "their YC page", "text": "\n".join(p for p in (one_liner, desc) if p),
                    "url": yc, "date": ""})
    for launch in launches:
        body = f"{getattr(launch, 'title', '')}\n{getattr(launch, 'body', '')}".strip()
        raw.append({"label": "their launch post", "text": body, "url": yc, "date": ""})
    for item in news:
        raw.append({"label": f"news ({getattr(item, 'source', None) or 'unknown outlet'})",
                    "text": getattr(item, "title", ""), "url": getattr(item, "url", "") or "",
                    "date": _date(getattr(item, "published_at", None))})
    bio = (getattr(founder, "bio", None) or "").strip()
    if bio:
        raw.append({"label": "the founder's YC bio", "text": bio, "url": yc, "date": ""})
    for r in company_web_results(company, search, company_repo, now):
        raw.append({"label": "web: " + (r.get("title") or "a page about them"),
                    "text": r.get("content", ""), "url": r.get("url", ""),
                    "date": r.get("published_date", "")[:10]})
    name = company_display_name(getattr(company, "name", "") or "")
    for r in founder_web_results(founder, name, search, founder_repo, now):
        raw.append({"label": "web, about the founder: " + (r.get("title") or ""),
                    "text": r.get("content", ""), "url": r.get("url", ""),
                    "date": r.get("published_date", "")[:10]})
    sources = []
    for s in raw:
        text = (s["text"] or "").strip()[:MAX_SOURCE_CHARS]
        if text:
            sources.append({**s, "text": text, "id": f"S{len(sources) + 1}"})
    return sources


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9+-]*", (text or "").casefold())
            if w not in _STOP and len(w) > 2}


def fact_problem(fact: str, source_text: str, extra_names: str = "") -> str | None:
    """Why `fact` is not supported by `source_text`, or None when it is.

    Deterministic and deliberately strict: a fact must share real words
    with its source, every number in it must be in the source, and every
    capitalised name in it must be in the source (or be the company's or
    founder's own name). A model that "summarises" a source into a new
    claim fails here."""
    text = (fact or "").strip()
    if not text:
        return "empty"
    source = (source_text or "").casefold()
    shared = _terms(text) & _terms(source)
    if len(shared) < min(2, len(_terms(text))):
        return "shares too little with its source"
    for number in re.findall(r"\d[\d,.]*", text):
        if number.strip(".,") not in source:
            return f"the number {number} is not in its source"
    allowed = f"{source} {extra_names.casefold()}"
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for index, word in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9.&+-]*", sentence)):
            clean = word.strip(".")
            if index == 0 or not clean[:1].isupper() or clean == "I":
                continue
            if clean.casefold() not in allowed:
                return f"names {clean}, which its source does not"
    return None


def _is_old(date: str, now: datetime) -> bool:
    try:
        when = datetime.fromisoformat(date[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return now - when >= OLD_AFTER


def run_researcher(researcher, company_name: str, founder_name: str, sources: list[dict],
                   now: datetime | None = None) -> dict:
    """The verified research for one letter. Never raises.

    `{facts: [{kind, text, source, label, url, old}], question,
    skill_connection, note, sources}`. `facts` may be empty - the note
    then says why, and the review screen shows it.
    """
    now = now or datetime.now(timezone.utc)
    result = {"facts": [], "question": "", "skill_connection": "", "note": "",
              "sources": [{k: s[k] for k in ("id", "label", "url", "date")} for s in sources]}
    if not sources:
        result["note"] = "Nothing to research from - no YC description, posts, news or web results."
        return result
    if researcher is None:
        result["note"] = "No researcher configured - written from their YC page only."
        return result
    try:
        answer = researcher.research({
            "company_name": company_name,
            "founder_name": founder_name,
            "today": now.date().isoformat(),
            "sources": sources,
        }) or {}
    except Exception as exc:  # noqa: BLE001 - research that did not arrive
        log.warning("research: researcher failed (%s)", type(exc).__name__)
        result["note"] = "Research failed this time - written from their YC page only."
        return result

    by_id = {s["id"]: s for s in sources}
    names = f"{company_name} {founder_name}"
    dropped = []
    for fact in answer.get("facts") or []:
        kind = str(fact.get("kind") or "")
        text = str(fact.get("text") or "").strip()
        source = by_id.get(str(fact.get("source") or "").strip())
        if kind not in FACT_KINDS or not text:
            continue
        problem = "cites no real source" if source is None else fact_problem(text, source["text"], names)
        if problem:
            dropped.append(f"{kind.replace('_', ' ')} ({problem})")
            continue
        if any(f["kind"] == kind for f in result["facts"]):
            continue
        old = bool(fact.get("old")) or _is_old(source.get("date", ""), now)
        result["facts"].append({"kind": kind, "text": text, "source": source["id"],
                                "label": source["label"], "url": source["url"], "old": old})

    question = str(answer.get("question") or "").strip()
    result["question"] = question if question.endswith("?") else ""
    result["skill_connection"] = str(answer.get("skill_connection") or "").strip()
    notes = [str(answer.get("note") or "").strip()]
    kinds = {f["kind"] for f in result["facts"]}
    if "specific_thing" not in kinds:
        notes.append("No specific named product, model or feature was found.")
    if "founder_fact" not in kinds:
        notes.append("No real detail about the founder was found.")
    if dropped:
        notes.append("Dropped as unsupported by its source: " + "; ".join(dropped) + ".")
    result["note"] = " ".join(n for n in notes if n)
    return result


def facts_text(research: dict | None) -> str:
    """Every verified fact as one block of text - what the checks treat as
    known about the company and founder."""
    if not research:
        return ""
    parts = [f["text"] for f in research.get("facts") or []]
    parts += [research.get("question") or "", research.get("skill_connection") or ""]
    return "\n".join(p for p in parts if p)


_READ_FRAMING = re.compile(r"\bI(?:'ve)? read\b|\bread that\b|\bread about\b|\bI came across\b", re.I)


def research_problems(company_part: str, research: dict | None, sources: list[dict],
                      company_name: str = "", founder_name: str = "") -> list[str]:
    """His checklist's research items, over the company part of a letter:

    - every name in it comes from a source (or is the company, the
      founder, or something in his own letter);
    - an OLD fact is framed as "I read that ...", not "you do ...".

    Error strings, for the drafter's retry, like `validate_draft`'s."""
    from app.domain.letter import ABOUT_HIM

    text = (company_part or "").strip()
    if not text:
        return []
    errors = []
    known = " ".join(s.get("text", "") for s in sources).casefold()
    allowed = f"{known} {facts_text(research).casefold()} {ABOUT_HIM.casefold()} " \
              f"{company_name.casefold()} {founder_name.casefold()}"
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for index, word in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9.&+-]*", sentence)):
            clean = word.strip(".")
            if index == 0 or not clean[:1].isupper() or clean == "I":
                continue
            if len(clean) <= 3 and clean.isupper():
                continue
            if clean.casefold() not in allowed:
                errors.append(f"names {clean}, which no source mentions - use only the verified facts")
                break
    for fact in (research or {}).get("facts") or []:
        if fact.get("old") and len(_terms(fact["text"]) & _terms(text)) >= 3 \
                and not _READ_FRAMING.search(text):
            errors.append('uses an OLD fact as if it were current - frame it as "I read that ..."')
            break
    return errors
