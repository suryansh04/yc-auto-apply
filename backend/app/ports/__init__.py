"""Port Protocols for every external service the pipeline depends on.

Ports are pure structural-typing interfaces (D8) — no implementation, no
framework imports. Real implementations live in `app.adapters`; offline
fakes for testing live in `tests.fakes`. `domain/` and `application/` may
depend only on these Protocols, never on a concrete adapter.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable


class QuotaExhausted(Exception):
    """Raised by an `EmailVerifier` when its provider signals no
    verification credits remain (D30). Lives here, not in
    `app.adapters.email_verifier`, so `ResolveEmails` (application/) can
    catch it without importing a concrete adapter (D8) — this module is
    the shared contract both layers already depend on for the `EmailVerifier`
    Protocol itself."""


@runtime_checkable
class YCDirectory(Protocol):
    def fetch_companies(self) -> list[dict]: ...


@runtime_checkable
class CompanyPageSource(Protocol):
    def fetch_page(self, slug: str) -> dict: ...  # returns the parsed `props`


@runtime_checkable
class EmailVerifier(Protocol):
    """Resolves whether an address is deliverable.

    Backed by a third-party verification provider (D3/D30); the provider is
    configuration, not part of this interface.
    """

    def verify(self, address: str) -> str: ...  # valid|invalid|catch_all|unknown

    def remaining_quota(self) -> int | None: ...


@runtime_checkable
class Drafter(Protocol):
    def draft(self, prompt_context: dict) -> dict: ...  # {subject, body, research_source_type, research_source_id}


@runtime_checkable
class ResumeParser(Protocol):
    def parse(self, raw_text: str) -> list[dict]: ...  # [{kind, text, source_span}]


@runtime_checkable
class MailSender(Protocol):
    def send(
        self, to: str, subject: str, body: str, attachment_path: str | None
    ) -> tuple[str, str]: ...  # (message_id, thread_id)


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
