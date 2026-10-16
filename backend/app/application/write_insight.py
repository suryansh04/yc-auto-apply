"""The company paragraph, written from the company's own YC description.

Operator request (2026-09-23): the **AI suggestion** tab on the company
paragraph had nothing to offer. It was asking the `Reviser`, which only
ever rewrites a span a mechanical check flagged - and that paragraph is
usually fine as English, so the honest answer was "nothing sharper to
offer". What the operator wants there is a different question asked of
the model: read what the founders wrote about their own company and
write the paragraph again from scratch.

The source is `companies.long_description`, synced from the YC dataset
(`sync_companies`) - the block of text the founders wrote themselves,
which the operator pointed at directly. Measured before this was built:
148 companies in the database, 139 with a description, 542 characters on
average, 2432 at most.

Pure application logic - depends on the `InsightWriter` port, never on a
concrete adapter, so this module never imports `app.adapters`.

**The same rule that governs `suggest_fixes`:** this is advice, and advice
can never fail loudly. `run_insight_writer` never raises. A writer that
throws (bad key, malformed response, no network) is logged by exception
*type name only* - never `str(exc)`, which could carry provider detail -
and yields nothing, leaving the letter exactly as it was.

What comes back is not trusted to be usable. A paragraph is dropped when:

- it is empty, which the prompt asks for when the description is too thin
  to say anything true (a generic line already exists for that case);
- it is too short or too long to be the paragraph it replaces;
- it carries the tells the letter bans everywhere else - a banned phrase,
  a claim to have used the product, a reference to how the sender found
  them (`app.domain.validation`);
- it quotes a number out of the description. Every number in there is a
  fundraising fact - ARR, growth, headcount, market size - and reciting
  one back at a founder is the opposite of the paragraph's job. The
  prompt forbids it; this is the check that makes the ban real.
- it is not plain text (markdown, an em dash, a smart quote), which the
  letter is pinned to be (D10);
- it reaches for the worked example's template ("on the same side ...
  which is the kind of thing I want to build"). See `_FORMULA_TELLS`.
"""

from __future__ import annotations

import logging
import re

from app.application.research_company import facts_text
from app.domain.validation import (
    BANNED_PHRASES,
    SOURCE_REFERENCE_PHRASES,
    TRIED_IT_PHRASES,
)

log = logging.getLogger(__name__)

# The paragraph it replaces is one of eight in the letter. Below the floor
# it says nothing; above the ceiling it is no longer a paragraph in a cold
# email that is already close to its word budget (`validation.MAX_WORDS`).
# 18, not 25 (2026-09-25): the "short" option is two sentences by design -
# "I got curious about how WareIQ decides which warehouse stock goes to
# before an order is placed. That's the kind of problem I want to work on."
#
# 2026-09-25: an option is now the whole company part of his template -
# the tie-in and the curious paragraph together - so the bounds are for
# both. His own WareIQ version is 56 words.
MIN_WORDS = 25
MAX_WORDS = 130   # the checklist's 50-110 (validation.company_part_problems) applies first
MAX_PARAGRAPHS = 2

# Operator decision (2026-09-25): ONE number is allowed - a fact about
# what the product does, as in his own "you sync orders across 50+
# channels". What stays banned is the fundraising kind: money, growth,
# percentages, and anything counted in customers or users.
MAX_NUMBERS = 1
_INVESTOR_FACT = re.compile(
    r"%|\bpercent|[$₹]|\b(?:rs\.?|inr|usd|crore|cr|lakh|million|billion|bn|arr|mrr|"
    r"revenue|raised|funding|valuation|growth|grew|growing|customers?|users?|clients?|"
    r"downloads?|investors?)\b",
    re.I,
)

# D10: the letter is plain text. A model that answers in markdown has
# written something that cannot be pasted into the body as it stands.
_NOT_PLAIN_TEXT = re.compile(r"[*_#`—‘’“”]|^\s*[-•]\s", re.MULTILINE)

# A digit that is part of a fact, not part of a name (Y42, 3M, GPT-4).
_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d[\d,.]*\s*(?:%|percent|x|k|m|bn|b|cr|crore|lakh)?", re.I)

# How the sender found them is never what a sentence is about (D29). The
# operator's own generic line opens "I came across {company}", which is
# why that phrase is not in `SOURCE_REFERENCE_PHRASES` - he writes it
# himself, deliberately, in the line that claims nothing. A paragraph the
# model wrote to be SPECIFIC has no business starting there.
# 2026-09-25: "I came across", "I read about" and "I was reading" are no
# longer here - his pipeline document's own examples open that way ("I
# came across Aadi on Hugging Face", "I read about Kosh's joint liability
# model"), because with verified research they are simply true.
_DISCOVERY_TELLS = (
    "i stumbled", "i found your", "i saw your", "while looking for", "came across your",
    "your yc page", "y combinator", "your website says", "according to your",
)

# Live finding (2026-09-23): the operator read the paragraphs for
# Orangewood Labs and zingbus and said "it is showing the same for
# orangewood and zingbus". They were not the same sentence - they were
# the same TEMPLATE, taken from the worked example in the prompt: "I like
# that you put X on the same side as Y", then the ordinary world, then
# "which is the kind of thing I want to build". A founder who has read
# one of those has read all of them, which is precisely the thing this
# paragraph exists to avoid.
#
# The prompt bans them and this is what makes the ban real. The operator's
# own Loop Health line uses "on the same side" and is not affected: that
# line is already written into his letter, and what is banned here is a
# MODEL reaching for it again on a company it does not fit.
# Round two (2026-09-23), same complaint about a different template: the
# operator read "You could have just listed operators without touching
# their schedules. Usually an aggregator dumps ... Instead you built ..."
# and said "no this looks properly ai generated". It is the symmetry that
# gives it away - a formal setup, "usually", then "instead you". What he
# wants is the contrast inside one spoken sentence: "I really like how
# you're not dumping every route into a messy list and instead actually
# built a dedicated platform."
_FORMULA_TELLS = (
    "and you did not",
    "and you didn't",
    # Round three (2026-09-24): lifted from the prompt's own worked example
    # and written back for Inito, a company the example had nothing to do
    # with. The example no longer says it; this makes sure nothing does.
    "unglamorous",
    "could have taken the obvious route",
    "the thing that stuck with me",
    "on the same side",
    "kind of thing i want to build",
    "sort of thing i want to build",
    "kind of system i want to build",
    "kind of interface i want to build",
    "which is the interface i want",
    # "That's the kind of problem I want to work on" is NOT here any more
    # (2026-09-25): it closes two of the three examples the operator asked
    # the options to be like, and it was his own bridge line once. What
    # made the first template a template was the whole shape, and those
    # parts are still refused above.
)

_TELLS = tuple(
    p.casefold()
    for p in (*BANNED_PHRASES, *SOURCE_REFERENCE_PHRASES, *TRIED_IT_PHRASES, *_DISCOVERY_TELLS)
)


def unusable_reason(paragraph: str, description: str | None = None) -> str | None:
    """Why this answer cannot be offered, or None when it can.

    His pipeline document's checklist first (2026-09-25) - the same one a
    draft is held to (`validation.company_part_problems`) - then the
    older checks below it."""
    from app.domain.validation import company_part_problems

    problems = company_part_problems(paragraph or "")
    if (paragraph or "").strip() and problems:
        return problems[0]
    return _older_reason(paragraph, description)


def _older_reason(paragraph: str, description: str | None = None) -> str | None:
    """Why this answer cannot be offered, or None when it can.

    A reason string, not a boolean, because the API logs it - a paragraph
    silently vanishing was the thing being fixed here, and "the model
    wrote a number" must not look the same as "the model never answered".
    """
    text = (paragraph or "").strip()
    if not text:
        return "the model had nothing specific to say about this company"
    if len([p for p in text.split("\n\n") if p.strip()]) > MAX_PARAGRAPHS:
        return f"more than {MAX_PARAGRAPHS} paragraphs"
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
    # Numbers (2026-09-25): any that the facts contain ("49 plant
    # genomes", "50+ channels"), never an investor number.
    numbers = _NUMBER.findall(text)
    if numbers:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if _NUMBER.search(sentence) and _INVESTOR_FACT.search(sentence):
                return "quotes an investor number (money, growth, customers)"
        known = re.sub(r"[^0-9]+", " ", description or "").split()
        for number in numbers:
            digits = re.sub(r"[^0-9]", "", number)
            if description is not None and digits and digits not in known:
                return "quotes a number that is not in what they wrote"
    for phrase in _FORMULA_TELLS:
        if phrase in folded:
            return f"reuses the template every other letter would get ({phrase!r})"
    return None


def run_insight_writer(
    writer, company_name: str, one_liner: str, description: str,
    avoid: list[str] | None = None, built: str = "", research: dict | None = None,
) -> dict | None:
    """Ask for company paragraphs, and return the usable ones.

    Operator request (2026-09-25): three options rather than one - the
    excited one, a plainer "curious" one that admits he does not know how
    they do it yet, and a short one - so he picks the one that sounds most
    like him. Each is checked on its own; the ones that fail are dropped
    and the rest are offered.

    `built` is the list of what he has built, from his own letter. It is
    the ONLY thing about him an option may mention, so nothing about his
    background can be invented here.

    `avoid` is what this company has already been given - the paragraph in
    the letter now, and whatever a previous press of "Another" produced.

    When every option is refused and at least one for reaching for a
    template, it is asked ONE more time with the refused text named.
    Silence is a worse answer than a second attempt, and that failure is
    one the model can correct. Every other refusal stands.

    `{options: [{style, paragraph}], why}` with at least one option, or
    None. Never raises (see module docstring).
    """
    if not (description or "").strip():
        log.info("insight: %s has nothing to write from", company_name)
        return None

    seen = [a.strip() for a in (avoid or []) if a and a.strip()]
    for attempt in (1, 2):
        try:
            answer = writer.write({
                "company_name": company_name,
                "one_liner": one_liner or "",
                "description": description,
                "built": built or "",
                "research": research,
                "avoid": list(seen),
            })
        except Exception as exc:  # noqa: BLE001 - a provider failure is advice that did not arrive
            # Type name only: a provider's message can carry a key.
            log.warning("insight: writer failed (%s)", type(exc).__name__)
            return None

        usable, retry_with = [], []
        for option in _options_of(answer):
            paragraph = option["paragraph"].strip()
            known = "\n".join(p for p in (description, facts_text(research)) if p)
            reason = _rejection(paragraph, seen + [u["paragraph"] for u in usable], known)
            if reason is None:
                usable.append({**option, "paragraph": paragraph})
                continue
            log.info("insight: dropped a %s option for %s - %s",
                     option["style"] or "unlabelled", company_name, reason)
            if paragraph and _worth_retrying(reason):
                retry_with.append(paragraph)
        if usable:
            return {"options": usable, "why": str((answer or {}).get("why") or "").strip()}
        if attempt == 2 or not retry_with:
            return None
        seen.extend(retry_with)
    return None


def _options_of(answer) -> list[dict]:
    """The options out of whatever the adapter returned - its own shape,
    or the older single paragraph a test fake may still hand back."""
    answer = answer or {}
    if isinstance(answer.get("options"), list):
        raw = answer["options"]
    elif "paragraph" in answer:
        raw = [{"style": "excited", "paragraph": answer.get("paragraph")}]
    else:
        raw = []
    return [{**{k: str(v or "") for k, v in o.items()},
             "style": str(o.get("style") or ""), "paragraph": str(o.get("paragraph") or "")}
            for o in raw if isinstance(o, dict)]


def _rejection(paragraph: str, seen: list[str], description: str | None = None) -> str | None:
    """`unusable_reason`, plus "we have already offered this one"."""
    reason = unusable_reason(paragraph, description)
    if reason is not None:
        return reason
    folded = paragraph.strip().casefold()
    if any(folded == a.strip().casefold() for a in seen):
        return "the same paragraph the letter already has"
    return None


def _worth_retrying(reason: str) -> bool:
    return reason.startswith("reuses the template") or reason.startswith("the same paragraph")
