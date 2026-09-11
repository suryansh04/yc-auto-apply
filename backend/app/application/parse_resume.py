"""Task 13: parse the resume into structured, citable claims (spec §10, D23).

`ParseResume` is pure application logic: it depends only on the
`ResumeParser` port and a resume-profile repo, both injected by the caller
(D8) — never on a concrete adapter, so this module never imports
`app.adapters`. The CLI wires `GeminiResumeParser` and the SQLAlchemy
`ResumeProfileRepo` together (see `app.cli.parse_resume`).

Claims are the *only* things the drafter may assert about the sender
(spec §10 part 2, D17). Each claim carries the verbatim `source_span` it
was extracted from, so any sentence in an outbound email can be traced back
to a literal line of the resume. That trail is only worth anything if the
span really is verbatim, so this use case checks it rather than trusting
the parser: a claim whose `source_span` does not occur in `raw_text` — a
paraphrase, a hallucinated span, an empty string — is kept but *flagged*
(`ResumeClaim.span_verified = False`, also persisted as `span_verified` in
the JSONB claim dict, and collected on `self.unverified` so `show-claims`
can point the operator at it). Flagged rather than dropped because the
brief's own first test feeds "resume text" against a span that cannot
occur in it and still expects the claim back; and because the claim's
*text* may well be true even when the parser botched the span — the
operator reads every claim before any draft is made (task-13 brief, step
5), and `DraftOutreach` excludes unverified claims from the drafter unless
the operator opts in with `draft --keep-unverified` (fix round 1, R3).
The match is case-sensitive but whitespace-normalised (fix round 1, F8):
`pypdf` wraps lines and doubles spaces mid-sentence, and a span that
crosses a wrapped line is still the same span. A claim with a `kind`
outside the spec's five (`skill | project | metric | role | education`) or
with empty `text` IS dropped (collected on `self.dropped`, never
persisted): a claim the drafter can't classify or can't cite is not a
claim.

Two guards protect the single `resume_profile` row from being replaced by
nothing: blank input is rejected outright (`ValueError`) before the parser
is ever called, and a parser run that yields zero usable claims raises
before `save` (fix round 1, F6) — a flaky re-parse must never wipe the only
copy of a good profile and silently disable drafting.
"""

import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone

from app.domain.entities import ResumeClaim

log = logging.getLogger(__name__)

CLAIM_KINDS = frozenset({"skill", "project", "metric", "role", "education"})
_WHITESPACE = re.compile(r"\s+")


class _UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _normalise_ws(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


class ParseResume:
    def __init__(self, parser, repo, clock=None):
        self._parser = parser
        self._repo = repo
        self._clock = clock or _UTCClock()
        self.dropped: list[dict] = []
        self.unverified: list[dict] = []

    def run(self, raw_text: str) -> list[ResumeClaim]:
        if not raw_text or not raw_text.strip():
            raise ValueError("resume text is empty")

        self.dropped, self.unverified = [], []
        claims: list[ResumeClaim] = []
        normalised_resume = _normalise_ws(raw_text)
        for raw in self._parser.parse(raw_text):
            claim = self._validate(raw, normalised_resume)
            if claim is None:
                self.dropped.append(dict(raw))
                continue
            if not claim.span_verified:
                self.unverified.append(dict(raw))
            claims.append(claim)

        if not claims:
            raise ValueError(
                "parser produced no usable claims; existing profile not replaced"
            )

        self._repo.save(raw_text, [asdict(c) for c in claims])
        if self.dropped:
            log.warning("dropped %d claim(s) with an unknown kind or empty text",
                        len(self.dropped))
        if self.unverified:
            log.warning("%d claim(s) have a source_span not found verbatim in the resume",
                        len(self.unverified))
        return claims

    @staticmethod
    def _validate(raw: dict, normalised_resume: str) -> ResumeClaim | None:
        kind = str(raw.get("kind") or "").strip().lower()
        text = str(raw.get("text") or "").strip()
        span = str(raw.get("source_span") or "")
        if kind not in CLAIM_KINDS:
            return None
        if not text:
            return None
        # Verbatim means same characters, same case — but not same
        # whitespace: a span crossing a PDF line wrap is still verbatim.
        normalised_span = _normalise_ws(span)
        verified = bool(normalised_span) and normalised_span in normalised_resume
        return ResumeClaim(kind=kind, text=text, source_span=span, span_verified=verified)
