"""Turn a check's note into the exact words it is about.

Operator request (2026-09-22): "mark the email with a yellowish
highlighted part where there is an issue and it gives suggestion like
grammarly".

A note like `a sentence exceeds 45 words: 'I read TechNode Global about'`
already names the text it objects to - the validator has been quoting it
all along, it was just never used for anything. This module pulls that
quote back out and resolves it to a REAL substring of the body, so the
screen can wrap those characters and nothing else.

Two rules make this safe to render:

- A span is only returned when it is found in the body verbatim. A quote
  the validator truncated, lower-cased or paraphrased resolves to
  nothing rather than to the wrong sentence, and the note is still shown
  without a highlight. Marking the wrong words would be worse than
  marking none.
- Spans never overlap, because a highlight inside a highlight renders as
  neither. A PRECISE span (the phrase the note quoted) always beats a
  COARSE one (the whole company paragraph, for a note that names no
  phrase); between two precise spans the longer wins, so a sentence beats
  a word inside it.
"""

from __future__ import annotations

import re

from app.domain.validation import _hook, _sentences

# The validator quotes with `!r`, so a quote is 'single' or "double".
# Non-greedy, and the closing quote must end the note or be followed by a
# separator, so an apostrophe inside the quoted text does not truncate it.
_QUOTED = re.compile(r"""(['"])(.+?)\1(?=\s*$|[.,;:]|\s+in\s)""", re.DOTALL)

# Notes that are about the company paragraph as a whole - there is no
# phrase to point at, but there IS a region, and it is the one the
# operator would want to look at.
_HOOK_NOTES = (
    "hook shares no distinctive term",
    "hook overlaps more than",
    "hook is a near-restatement",
)

# Notes about the whole letter. Highlighting all of it would be noise.
_WHOLE_BODY_NOTES = (
    "body exceeds",
    "more than",
    "fewer than",
    "subject",
)


def _find_verbatim(body: str, needle: str) -> str | None:
    """`needle` as it actually appears in `body`, or None.

    Tries the exact text first, then case-insensitively - the phrase bans
    match against a lower-cased body, so `banned phrase: 'genuinely'` has
    to be able to find "Genuinely" at the start of a sentence.
    """
    if not needle:
        return None
    if needle in body:
        return needle
    match = re.search(re.escape(needle), body, re.IGNORECASE)
    return match.group(0) if match else None


# The validator truncates a quoted sentence to 40 characters
# (`{s[:40]!r}`). A quote at or near that length with no terminal
# punctuation was almost certainly cut mid-sentence.
_TRUNCATION_LENGTH = 40
_LOOKS_COMPLETE = re.compile(r"[.!?][\"']?\s*$")


def _was_truncated(fragment: str) -> bool:
    return len(fragment) >= _TRUNCATION_LENGTH - 5 and not _LOOKS_COMPLETE.search(fragment)


def _sentence_containing(body: str, fragment: str) -> str | None:
    """The whole sentence a TRUNCATED quote came from.

    Highlighting half a sentence and stopping mid-word is jarring, so a
    cut quote is grown back. A quote that was never cut - a banned
    phrase, a single word - is left exactly as it is: marking a whole
    sentence because one word in it is wrong points at the wrong thing.
    """
    found = _find_verbatim(body, fragment)
    if found is None or not _was_truncated(fragment):
        return found
    for sentence in _sentences(body):
        if found in sentence:
            return sentence
    return found


def _span_and_precision(note: str, body: str) -> tuple[str | None, bool]:
    """The substring this note is about, and whether it is precise.

    Precise means "the words the note actually quoted". Coarse means "the
    region the note is about" - only the company paragraph, for notes that
    name no phrase at all.
    """
    low = note.lower()

    if any(low.startswith(prefix) or prefix in low for prefix in _HOOK_NOTES):
        hook = _hook(body)
        return (hook if hook and hook in body else None), False

    if "semicolon" in low:
        return _find_verbatim(body, ";"), True

    if "bracketed citation" in low:
        match = re.search(r"\[\s*\d+\s*\]", body)
        return (match.group(0) if match else None), True

    if any(low.startswith(prefix) for prefix in _WHOLE_BODY_NOTES):
        return None, True

    # Everything else: the validator quoted the offending text. Take the
    # LAST quote - "asserts something found in no resume claim: 'Postgres'
    # in 'My closest project is a campus event dir'" names the token
    # first and the sentence second, and the sentence is what to mark.
    quotes = [m.group(2) for m in _QUOTED.finditer(note)]
    if not quotes:
        return None, True
    fragment = quotes[-1]
    found = _sentence_containing(body, fragment) or _find_verbatim(body, fragment)
    return found, True


def span_for(note: str, body: str) -> str | None:
    """The substring of `body` this note is about, or None."""
    return _span_and_precision(note, body)[0]


def highlights_for(notes: list[str], body: str) -> list[dict]:
    """One entry per note: `{message, quote}`.

    `quote` is an exact substring of `body`, or None when the note is
    about the letter as a whole (its length, its shape) or its quote
    could not be resolved. Overlapping spans are dropped, longest kept,
    so the renderer never has to nest a mark inside a mark.
    """
    resolved = [(note, *_span_and_precision(note, body)) for note in notes]

    # Precise spans first - a note that quoted the exact words beats one
    # that could only name the paragraph, or the semicolon disappears
    # under a highlight of the whole letter. Longest first within each
    # group, so a sentence beats a word inside it.
    ranked = sorted(
        (i for i, (_, span, _p) in enumerate(resolved) if span),
        key=lambda i: (resolved[i][2], len(resolved[i][1])),
        reverse=True,
    )
    taken: list[tuple[int, int]] = []
    keep: set[int] = set()
    for i in ranked:
        span = resolved[i][1]
        start = body.find(span)
        end = start + len(span)
        if start < 0 or any(start < t_end and t_start < end for t_start, t_end in taken):
            continue
        taken.append((start, end))
        keep.add(i)

    return [
        {"message": note, "quote": span if i in keep else None}
        for i, (note, span, _precise) in enumerate(resolved)
    ]
