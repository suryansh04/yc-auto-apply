"""Paragraph layout repair for model output.

Live finding (2026-09-16): with the curious-and-specific voice, Sarvam
returned 11 of 44 drafts as ONE block - hook, self-line and ask separated
by single newlines or nothing - and repeated the mistake on retry, so
good emails landed in "needs manual write" for a formatting slip. The
three parts are structurally recognisable, so the split is deterministic:
the self-line starts the first sentence beginning with "I" after the
hook, and the ask is the final sentence. Content is never changed.
"""

from __future__ import annotations

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SELF_START = re.compile(r"^I(?:'m|'ve|'d|'ll|\b)")


def normalise_paragraphs(body: str) -> str:
    text = body.replace("\r\n", "\n").strip()
    if not text:
        return text
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) >= 2:
        return "\n\n".join(paragraphs)

    # Single newlines are the model's idea of a paragraph break.
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) >= 2:
        return "\n\n".join(lines)

    # One flat block: split by the roles the parts play.
    sentences = _SENTENCE_SPLIT.split(text)
    if len(sentences) < 3:
        return text
    self_idx = next(
        (i for i, s in enumerate(sentences) if i > 0 and _SELF_START.match(s)), None
    )
    if self_idx is None or self_idx >= len(sentences) - 1:
        return text
    hook = " ".join(sentences[:self_idx])
    middle = " ".join(sentences[self_idx:-1])
    ask = sentences[-1]
    return "\n\n".join(p for p in (hook, middle, ask) if p)
