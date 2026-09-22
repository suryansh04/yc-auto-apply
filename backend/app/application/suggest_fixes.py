"""The rewrite behind each amber highlight.

Operator request (2026-09-22): "when i hover over it, it shows suggestion
just like grammarly use sarvam for suggestion". The deterministic checks
(`app.domain.validation`) say what is wrong and
`app.domain.highlight` says which words they mean; this module asks the
`Reviser` port (`app.ports`) what to write instead, so the hover card can
offer a replacement rather than only a complaint.

Pure application logic - depends on the port, never on a concrete
adapter, so this module never imports `app.adapters`.

**The single rule that governs this module, the same one `critique_draft`
follows:** this is advice, and advice can never fail loudly. `run_reviser`
never raises. A reviser that throws (bad key, malformed response, no
network) is logged by *exception type name only* - never `str(exc)`,
which could carry provider detail - and yields no suggestions, leaving
the checks already on screen exactly as they were.

What comes back from a model is not trusted to be usable. A suggestion is
kept only when:

- its `index` names a span that was actually sent, so a hallucinated
  index cannot attach a rewrite to the wrong sentence;
- the replacement is not the flagged text over again (whitespace and case
  ignored), because "change it to what it already says" is noise in a
  hover card; and
- the replacement is not absurdly longer than what it replaces - a model
  that answers with the whole paragraph, or the whole letter, has not
  understood that it is rewriting one span, and pasting that in would
  wreck the letter.

An empty replacement is kept deliberately - "delete this" is a real
suggestion, and the prompt asks for it - but only for a SHORT span. Live
finding (2026-09-23, Loop Health, outreach 64): asked about a coarse
note, the note that names no phrase and so points at the whole company
paragraph, Sarvam answered with an empty replacement. Pressing "Cut it"
would have deleted the only part of the letter that is about the founder's
company. Deleting a paragraph is not a rewrite of it, so an empty
replacement for a long span is dropped like any other unusable answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# A rewrite may grow, but a span rewritten into four times its own length
# is not a rewrite of that span. Generous on purpose: the shortest spans
# (a semicolon, one banned word) legitimately expand by a lot, hence the
# floor below.
_MAX_GROWTH = 4.0
_MIN_ROOM = 120  # characters a replacement may always use, however short the span
# The longest span "cut this" is an honest answer for: a phrase or a
# sentence, never a paragraph (see the module docstring's live finding).
_MAX_DELETABLE = 200


@dataclass(frozen=True)
class Suggestion:
    """One rewrite, addressed to the note it answers."""

    index: int       # position in the `highlights` list the spans came from
    replacement: str  # the new text for that span alone; "" means delete it
    why: str          # one short line for the hover card


def _usable(replacement: str, quote: str) -> bool:
    if replacement.strip().casefold() == quote.strip().casefold():
        return False
    if not replacement.strip():
        return len(quote) <= _MAX_DELETABLE
    return len(replacement) <= max(_MIN_ROOM, int(len(quote) * _MAX_GROWTH))


def run_reviser(reviser, body: str, highlights: list[dict], company_name: str = "") -> list[Suggestion]:
    """Ask for a rewrite of every highlighted span, and return the ones
    worth showing.

    `highlights` is `app.domain.highlight.highlights_for`'s output -
    `[{message, quote}]`, in order. Entries with no quote name no words to
    rewrite (a note about the letter's length, a quote that could not be
    resolved) and are not sent; their positions are still what `index`
    refers to, so a caller can pair a suggestion with its note by index
    alone.

    Never raises (see module docstring)."""
    spans = [
        {"index": i, "quote": h["quote"], "note": h.get("message") or ""}
        for i, h in enumerate(highlights)
        if h.get("quote")
    ]
    if not spans:
        return []

    try:
        raw = reviser.revise({"body": body, "company_name": company_name, "spans": spans})
    except Exception as exc:  # noqa: BLE001 - advisory pass, must never block the review
        log.warning("reviser failed: %s", type(exc).__name__)
        return []

    quotes = {span["index"]: span["quote"] for span in spans}
    out: list[Suggestion] = []
    seen: set[int] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if index not in quotes or index in seen:
            continue
        replacement = str(item.get("replacement") or "")
        if not _usable(replacement, quotes[index]):
            continue
        seen.add(index)
        out.append(Suggestion(index=index, replacement=replacement, why=str(item.get("why") or "")))
    return out
