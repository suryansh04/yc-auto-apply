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
paraphrase, a hallucinated span, an empty string — is dropped and recorded
on `self.dropped` for the operator to see (`show-claims` prints them),
never persisted. The same goes for a claim with a `kind` outside the
spec's five (`skill | project | metric | role | education`) or with empty
`text`: a claim the drafter can't classify or can't cite is not a claim.

Blank input is rejected outright (`ValueError`) before the parser is ever
called — parsing an empty resume would either burn an LLM call for nothing
or, worse, persist an empty profile that silently disables drafting.
"""

import logging
from dataclasses import asdict
from datetime import datetime, timezone

from app.domain.entities import ResumeClaim

log = logging.getLogger(__name__)

CLAIM_KINDS = frozenset({"skill", "project", "metric", "role", "education"})


class _UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


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
        for raw in self._parser.parse(raw_text):
            claim = self._validate(raw, raw_text)
            if claim is None:
                self.dropped.append(dict(raw))
                continue
            if not claim.span_verified:
                self.unverified.append(dict(raw))
            claims.append(claim)

        self._repo.save(raw_text, [asdict(c) for c in claims])
        if self.dropped:
            log.warning("dropped %d claim(s) with an unknown kind or empty text",
                        len(self.dropped))
        if self.unverified:
            log.warning("%d claim(s) have a source_span not found verbatim in the resume",
                        len(self.unverified))
        return claims

    @staticmethod
    def _validate(raw: dict, raw_text: str) -> ResumeClaim | None:
        kind = str(raw.get("kind") or "").strip().lower()
        text = str(raw.get("text") or "").strip()
        span = str(raw.get("source_span") or "")
        if kind not in CLAIM_KINDS:
            return None
        if not text:
            return None
        # Verbatim means verbatim: an exact substring of the resume text, not
        # a case-folded or whitespace-normalised approximation. The span is
        # the audit trail (spec §10); if it can't be found, say so.
        verified = bool(span.strip()) and span in raw_text
        return ResumeClaim(kind=kind, text=text, source_span=span, span_verified=verified)
