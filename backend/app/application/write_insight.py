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
  letter is pinned to be (D10).
"""

from __future__ import annotations

import logging
import re

from app.domain.validation import (
    BANNED_PHRASES,
    SOURCE_REFERENCE_PHRASES,
    TRIED_IT_PHRASES,
)

log = logging.getLogger(__name__)

# The paragraph it replaces is one of eight in the letter. Below the floor
# it says nothing; above the ceiling it is no longer a paragraph in a cold
# email that is already close to its word budget (`validation.MAX_WORDS`).
MIN_WORDS = 25
MAX_WORDS = 110

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
_DISCOVERY_TELLS = (
    "i came across", "i stumbled", "i found your", "i saw your", "i read about",
    "i was reading", "while looking for", "came across your", "your yc page",
    "y combinator", "your website says", "according to your",
)

_TELLS = tuple(
    p.casefold()
    for p in (*BANNED_PHRASES, *SOURCE_REFERENCE_PHRASES, *TRIED_IT_PHRASES, *_DISCOVERY_TELLS)
)


def unusable_reason(paragraph: str) -> str | None:
    """Why this answer cannot be offered, or None when it can.

    A reason string, not a boolean, because the API logs it - a paragraph
    silently vanishing was the thing being fixed here, and "the model
    wrote a number" must not look the same as "the model never answered".
    """
    text = (paragraph or "").strip()
    if not text:
        return "the model had nothing specific to say about this company"
    if "\n\n" in text:
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
    if _NUMBER.search(text):
        return "quotes a number from their YC page"
    return None


def run_insight_writer(writer, company_name: str, one_liner: str, description: str) -> dict | None:
    """Ask for a company paragraph, and return it only if it is usable.

    `{paragraph, why}` or None. Never raises (see module docstring).
    """
    if not (description or "").strip():
        log.info("insight: %s has no YC description to write from", company_name)
        return None

    try:
        answer = writer.write({
            "company_name": company_name,
            "one_liner": one_liner or "",
            "description": description,
        })
    except Exception as exc:  # noqa: BLE001 - a provider failure is advice that did not arrive
        # Type name only: a provider's message can carry a key.
        log.warning("insight: writer failed (%s)", type(exc).__name__)
        return None

    paragraph = str((answer or {}).get("paragraph") or "").strip()
    reason = unusable_reason(paragraph)
    if reason is not None:
        log.info("insight: dropped an answer for %s - %s", company_name, reason)
        return None
    return {"paragraph": paragraph, "why": str((answer or {}).get("why") or "").strip()}
