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


class VerifierError(Exception):
    """Raised by an `EmailVerifier` for any verification failure that is
    *not* quota exhaustion — a bad API key, a 4xx/5xx, a transport failure,
    a malformed response body. Fix round 1 (blocker): the real adapter used
    to let `httpx.HTTPStatusError`/`httpx.HTTPError` propagate straight out
    of `verify`, and httpx formats those exceptions with the full request
    URL — which embeds the provider API key in its path (D30) — so an
    uncaught one printed the key to stderr via a CLI traceback, or into any
    log that records the exception. `VerifierError` carries only `address`
    and, when known, the HTTP `status`; its message is built from those two
    fields alone, never from the underlying httpx exception's own message,
    so it is always safe to log, print, or str() including via an uncaught
    traceback. Like `QuotaExhausted`, it lives here rather than in
    `app.adapters.email_verifier` so `ResolveEmails` can catch it without
    importing a concrete adapter (D8)."""

    def __init__(self, address: str, status: int | None = None, detail: str | None = None):
        self.address = address
        self.status = status
        self.detail = detail
        parts = [f"verification failed for {address}"]
        if status is not None:
            parts.append(f"(HTTP {status})")
        if detail:
            parts.append(f"- {detail}")
        super().__init__(" ".join(parts))


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

    def remaining_quota(self) -> int | None:
        """Credits left in the current quota period, if the provider
        exposes that directly; `None` when it doesn't. Fix round 1 (minor):
        the real Phase 1 adapter (`MyEmailVerifierClient`) never learns this
        from MyEmailVerifier's `validate_single` responses, which carry no
        remaining-credits field — it always returns `None` until a
        `QuotaExhausted` has actually been raised, at which point it
        returns `0`. A provider whose API does expose a live count is free
        to return it here instead; nothing downstream assumes either
        behaviour beyond "a number means credits are known to be left,
        `None` means they aren't."
        """


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
