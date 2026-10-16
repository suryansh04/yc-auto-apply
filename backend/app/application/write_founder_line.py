"""The line about the founder personally (2026-09-25).

Operator request: "a web agent or web scraper where we can write something
about the founder and why we like them", pointing at a letter that said
"I found your ML background really interesting, and I felt a genuine
overlap with the kind of problems I enjoy solving."

Two steps, both here:

1. **What to write from** (`founder_material`). The founder's YC bio,
   which is already in the database for 229 of 284 founders (measured
   2026-09-25) and costs nothing. When the bio is missing or too short to
   say anything, a web search (`WebSearch` port, Tavily) for the founder
   at this company - and only results that name BOTH the founder and the
   company are kept, because a search for a name finds everyone with that
   name. What the search found is cached on the founder, so asking again
   spends nothing.
2. **The line** (`run_founder_line_writer`). A `FounderLineWriter` writes
   three options; each is checked here and the unusable ones dropped.

Pure application logic - ports only, never `app.adapters`. Like every
other writer, advice never fails loudly: nothing here raises, and a
provider failure is logged by exception type name only.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from app.domain.letter import ABOUT_HIM
from app.domain.names import company_name_variants
from app.domain.validation import BANNED_PHRASES

log = logging.getLogger(__name__)

# Below this a YC bio is a job title, not a story ("Co-founder, CTO").
MIN_BIO_CHARS = 80
MAX_WEB_RESULTS = 3
MIN_WORDS = 10
MAX_WORDS = 50

YC_BIO = "their YC bio"
WEB = "a web search"

_NOT_PLAIN_TEXT = re.compile(r"[*_#`—‘’“”;]|https?://|^\s*[-•]\s", re.MULTILINE)
_NUMBER = re.compile(r"\d[\d,.]*")
_MONEY = re.compile(r"[$₹]|\b(?:million|billion|crore|lakh|raised|funding|valuation)\b", re.I)

# How he learned it is never what the line is about, and a founder who
# reads "I saw on your LinkedIn" learns they were looked up.
_WHERE_FOUND = (
    "linkedin", "your profile", "your bio", "looked you up", "googled", "i read about you",
    "y combinator", "yc page", "your website", "an article", "podcast", "twitter",
    "stalk",
)
# Interest, yes. Worship, no - flattery is the fastest way for this line
# to read as generated.
_FLATTERY = (
    "legendary", "inspiring", "inspired by you", "visionary", "huge fan", "i admire",
    "impressive", "amazing", "incredible", "fascinating", "journey", "passionate",
    "rockstar", "genius", "trailblazer", "remarkable",
    # Awards and press are things said ABOUT them (live, 2026-09-25: "how
    # being named on the Forbes 30 under 30 list has shaped the way you
    # approach building UrbanKisaan").
    "forbes", "under 30", "30 under", "named on", "award", "featured in",
)
_PRIVATE = ("your family", "your wife", "your husband", "your kids", "your age",
            "your religion", "your health")

_TELLS = tuple(p.casefold() for p in (*BANNED_PHRASES, *_WHERE_FOUND, *_FLATTERY, *_PRIVATE))

_STOP = frozenset("""
a an and are as at be been but by for from had has have he her his i i'd i'm
i've in into is it it's its me my of on or our she so that that's the their
them they this to was we were what when where which who why with you you're
your also really just about very would love found felt kind problems enjoy
solving background interesting specifically because wanted write writing
genuine overlap hear shapes shaped build today way like much more most
""".split())


def founder_web_results(founder, company_name: str, search=None, founder_repo=None,
                        now: datetime | None = None) -> list[dict]:
    """What the web says about THIS founder at THIS company, as
    `[{title, url, content, published_date}]`. Cached on the founder as
    JSON, so it is searched at most once; a failed search is not cached,
    so it is tried again next time."""
    if getattr(founder, "web_researched_at", None) is not None:
        return _cached(getattr(founder, "web_research", None))
    if search is None:
        return []
    found = _search(founder, company_name, search)
    if found is None:
        return []
    if founder_repo is not None:
        founder_repo.set_web_research(founder.id, json.dumps(found),
                                      now or datetime.now(timezone.utc))
    return found


def _cached(text: str | None) -> list[dict]:
    """The cached search, or the older plain-text cache as one result."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except ValueError:
        pass
    return [{"title": "", "url": "", "content": text, "published_date": ""}]


def founder_material(founder, company_name: str, search=None, founder_repo=None,
                     now: datetime | None = None) -> tuple[str, str]:
    """`(material, source)` for the founder line, or `("", "")`.

    The YC bio when it says enough. Otherwise whatever a web search found
    about this founder at this company (`founder_web_results`), with the
    bio in front of it when there is one. Never raises.
    """
    bio = (getattr(founder, "bio", None) or "").strip()
    if len(bio) >= MIN_BIO_CHARS:
        return bio, YC_BIO
    results = founder_web_results(founder, company_name, search, founder_repo, now)
    web = "\n".join(
        f"- {r.get('title', '').strip()} ({r.get('url', '').strip()})\n  {r.get('content', '').strip()}"
        for r in results
    ).strip()
    if web:
        return "\n\n".join(p for p in (bio, web) if p), WEB
    if bio:
        return bio, YC_BIO
    return "", ""


def _search(founder, company_name: str, search) -> list[dict] | None:
    """Results that name BOTH the founder and the company, or None when
    the search itself failed."""
    name = (getattr(founder, "full_name", None) or "").strip()
    if not name:
        return []
    try:
        results = search.search(f'"{name}" {company_name} founder', max_results=6)
    except Exception as exc:  # noqa: BLE001 - a failed search is no material
        log.warning("founder line: web search failed (%s)", type(exc).__name__)
        return None
    surname = name.split()[-1].casefold()
    companies = [v.casefold() for v in company_name_variants(company_name)] or [company_name.casefold()]
    kept = []
    for r in results:
        folded = f"{r.get('title', '')}\n{r.get('content', '')}".casefold()
        if surname in folded and any(c in folded for c in companies):
            kept.append({k: str(r.get(k) or "") for k in ("title", "url", "content", "published_date")})
        if len(kept) >= MAX_WEB_RESULTS:
            break
    return kept


def unusable_reason(line: str, material: str, founder_name: str = "",
                    company_name: str = "") -> str | None:
    """Why this line cannot be offered, or None when it can."""
    text = (line or "").strip()
    if not text:
        return "nothing specific enough in their background"
    if "\n" in text:
        return "more than one paragraph"
    words = len(text.split())
    if words < MIN_WORDS:
        return f"too short ({words} words)"
    if words > MAX_WORDS:
        return f"too long ({words} words)"
    if _NOT_PLAIN_TEXT.search(text):
        return "not plain text"
    folded = text.casefold()
    for phrase in _TELLS:
        if phrase in folded:
            return f"uses a phrase the letter bans ({phrase!r})"
    first = (founder_name or "").split()[0].casefold() if founder_name else ""
    if first and re.search(rf"\b{re.escape(first)}\b", folded):
        return "uses their name - the greeting already does"
    known = (material or "").casefold()
    for number in _NUMBER.findall(text):
        if number.strip(".,") not in known:
            return "quotes a number that is not in their background"
    if _MONEY.search(text):
        return "talks about money"
    # Every capitalised name in it must come from their background, his
    # letter or the company - that is what stops a line inventing a past
    # employer for them or a project for him.
    allowed = f"{known} {ABOUT_HIM.casefold()} {company_name.casefold()}"
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for index, word in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9.&+-]*", sentence)):
            clean = word.strip(".")
            if index == 0 or not clean[:1].isupper() or clean == "I":
                continue
            # Two- and three-letter acronyms (ML, AI, CS) are how people
            # talk about a field, not a claim about a place.
            if len(clean) <= 3 and clean.isupper():
                continue
            if clean.casefold() not in allowed:
                return f"names something not in their background ({clean})"
    # It has to be about something they actually did.
    theirs = _terms(material) - _terms(ABOUT_HIM) - _terms(company_name) - _terms(founder_name)
    if not (theirs & _terms(text)):
        return "says nothing from their background"
    return None


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold())
            if w not in _STOP and (len(w) > 2 or w in {"ml", "ai", "iit", "vc"})}


def run_founder_line_writer(writer, founder_name: str, title: str, company_name: str,
                            material: str, source: str,
                            avoid: list[str] | None = None) -> dict | None:
    """`{options: [{style, line}], why}` with at least one usable option,
    or None. Never raises."""
    if not (material or "").strip():
        return None
    seen = [a.strip().casefold() for a in (avoid or []) if a and a.strip()]
    try:
        answer = writer.write({
            "founder_name": founder_name,
            "title": title or "",
            "company_name": company_name,
            "material": material,
            "source": source,
            "about_him": ABOUT_HIM,
            "avoid": list(avoid or []),
        })
    except Exception as exc:  # noqa: BLE001 - advice that did not arrive
        log.warning("founder line: writer failed (%s)", type(exc).__name__)
        return None
    usable = []
    for option in (answer or {}).get("options") or []:
        if not isinstance(option, dict):
            continue
        line = str(option.get("line") or "").strip()
        # A sentence starts with a capital (live, 2026-09-25: "your time
        # working as a trainee at CERN caught my attention...").
        line = line[:1].upper() + line[1:]
        reason = unusable_reason(line, material, founder_name, company_name)
        if reason is None and (line.casefold() in seen
                               or any(line.casefold() == u["line"].casefold() for u in usable)):
            reason = "already offered"
        if reason is not None:
            log.info("founder line: dropped a %s option - %s",
                     option.get("style") or "unlabelled", reason)
            continue
        usable.append({"style": str(option.get("style") or ""), "line": line})
    if not usable:
        return None
    return {"options": usable, "why": str((answer or {}).get("why") or "").strip()}
