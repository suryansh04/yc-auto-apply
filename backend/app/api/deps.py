"""The dependency seam every router is built on (Task 3).

`Repos` bundles every repo the routers need — a structural superset of the
per-use-case bundles in `app/application/*.py` (`ScrapeRepos`,
`ResolveRepos`, `DraftRepos`, `ReviewRepos`, `SendRepos`,
`ManualEmailRepos`), the same pattern `app/cli.py` already wires concrete
repos into. Routers depend on `get_repos`; `create_app(repos_factory=...)`
overrides it in tests so the whole API can run against the in-memory
`FakeRepos` with no database and no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings, get_settings

_engine = None


@dataclass
class Repos:
    """Every repo a router may need, plus the session that owns them."""

    founder: Any
    company: Any
    outreach: Any
    message: Any
    news: Any
    launch: Any
    job_posting: Any
    email_candidate: Any
    resume_profile: Any
    job: Any
    session: Any
    # Task 5: a pre-built `MailSender`, present only when a caller injected
    # one (the test harness's `fake_sender` fixture). `None` in production -
    # `app.api.routers.send` builds a real `GmailSender` itself, gated by
    # `require_send_config` below, the one time it is actually needed. This
    # is the seam that makes a real Gmail send structurally impossible from
    # the offline test suite: nothing here ever builds a real sender.
    sender: Any = None


def _session() -> Session:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url)
    return Session(bind=_engine)


def real_repos(session_factory: Callable[[], Session] = _session) -> Iterator[Repos]:
    """Wires concrete adapters/db repos into a `Repos` bundle for one
    request, exactly the way each `cli.py` command wires its own bundle —
    then closes the session when the request is done, on both the success
    and the exception path (a raise inside a route resumes this generator
    via `.throw()` at the `yield` below, which still reaches `finally`).

    Deliberately does NOT commit (fix round 2, point 3): a GET must not
    write, and a write route commits explicitly on its own action (Task 4's
    plan calls `repos.session.commit()` in each handler) rather than
    relying on an implicit commit here that it can't control the boundary
    of.

    `session_factory` defaults to the real `_session()` (a live DB
    connection) but can be swapped for a fake in a test that wants to check
    the close-on-every-path behaviour without a database — see
    `backend/tests/api/test_deps.py`."""
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyNewsRepo,
        CompanyRepo,
        EmailCandidateRepo,
        FounderRepo,
        JobPostingRepo,
        JobRepo,
        MessageRepo,
        OutreachRepo,
        ResumeProfileRepo,
    )

    session = session_factory()
    try:
        yield Repos(
            founder=FounderRepo(session),
            company=CompanyRepo(session),
            outreach=OutreachRepo(session),
            message=MessageRepo(session),
            news=CompanyNewsRepo(session),
            launch=CompanyLaunchRepo(session),
            job_posting=JobPostingRepo(session),
            email_candidate=EmailCandidateRepo(session),
            resume_profile=ResumeProfileRepo(session),
            job=JobRepo(session),
            session=session,
        )
    finally:
        session.close()


def get_repos() -> Iterator[Repos]:
    """FastAPI dependency. `create_app(repos_factory=...)` overrides this
    name in `app.dependency_overrides` — that override is the whole test
    seam (see `backend/tests/api/conftest.py`)."""
    yield from real_repos()


def on_gemini_free_tier(settings: Settings) -> bool:
    """Task 6 fix round 1 (ruling T6-3): true when `draft` would send
    founders' names, titles and bios to a free-tier Gemini key, which
    Google may use for training (D12). Shared between `app.cli.draft`
    (which lets an operator opt in for a small deliberate test run via
    `--allow-free-tier`) and the UI job runner (`app/api/jobs.py`, which
    has no such opt-in and always refuses) — the same extraction Task 5
    did for `require_send_config`/`require_gmail_config`, so `app/api/`
    never has to import `app.cli` (backwards coupling: `cli.py` is the
    operator adapter, not a library for the API layer) just to share this
    one predicate."""
    return settings.llm_provider == "gemini" and settings.gemini_tier == "free"


def build_drafter(settings: Settings):
    """Task 6 fix round 2 (ruling T6-6): the LLM-provider switch for a
    `Drafter` adapter (Sarvam or Gemini), shared between `app.cli._drafter`
    (used by `cli.py draft`) and the UI job runner (`app.api.jobs._draft`)
    so a new provider or a changed model default is written in exactly one
    place. `real_repos` above already wires concrete adapters into this
    module for a request's `Repos` bundle; building a drafter is the same
    kind of adapter-construction, not business logic, so it belongs beside
    it rather than being duplicated per caller (the same extraction this
    fix round already did for `on_gemini_free_tier`, and Task 5 did for
    `require_send_config`/`require_gmail_config`).

    Raises a plain `RuntimeError` (never a `typer`-specific exception —
    this module must stay usable from the API layer with no CLI
    dependency) naming the missing setting; `app.cli._drafter` translates
    that into `typer.BadParameter` for the CLI's own nice error
    formatting."""
    if settings.llm_provider == "sarvam":
        from app.adapters import sarvam

        if not settings.sarvam_api_key:
            raise RuntimeError("SARVAM_API_KEY is not set")
        return sarvam.SarvamDrafter(settings.sarvam_api_key, model=settings.sarvam_model)

    from google import genai

    from app.adapters.gemini import GeminiDrafter

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=settings.gemini_api_key)
    return GeminiDrafter(client, model=settings.gemini_model)


def build_classifier(settings: Settings):
    """Task 9: the identical LLM-provider switch as `build_drafter` above,
    for a `ReplyClassifier` adapter (Sarvam or Gemini) instead of a
    `Drafter` — kept beside it, not duplicated a third time, for the same
    reason `build_drafter`'s own docstring gives (T6-6)."""
    if settings.llm_provider == "sarvam":
        from app.adapters import sarvam

        if not settings.sarvam_api_key:
            raise RuntimeError("SARVAM_API_KEY is not set")
        return sarvam.SarvamReplyClassifier(settings.sarvam_api_key, model=settings.sarvam_model)

    from google import genai

    from app.adapters.gemini import GeminiReplyClassifier

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=settings.gemini_api_key)
    return GeminiReplyClassifier(client, model=settings.gemini_model)


def require_send_config(settings: Settings) -> list[str]:
    """Task 5: every `Settings` field `send` needs before it may build a
    real `GmailSender` — the Gmail OAuth client config plus a real,
    non-blank signature (spec §18) and an existing resume PDF. Every one
    of these defaults to empty in `app.config` (so a fresh `.env` can
    never send by accident); this returns the NAMES of whichever are
    still missing/invalid, never their values, and an empty list means
    "go ahead". `app.cli.send`/`gmail-auth` and
    `app.api.routers.send` both call this — it used to be duplicated as
    `app.cli._missing_gmail_config` / `_require_send_config`; now there is
    exactly one rule for "configured enough to send" and the two callers
    cannot drift on it. Storing a refresh token is checked separately by
    each caller (a DB row via `GmailCredentialRepo`, not a `Settings`
    field)."""
    from pathlib import Path

    missing = []
    if not settings.gmail_sender_address:
        missing.append("GMAIL_SENDER_ADDRESS (the Gmail account authorised by gmail-auth)")
    if not settings.gmail_client_secrets_path or not Path(settings.gmail_client_secrets_path).is_file():
        missing.append("GMAIL_CLIENT_SECRETS_PATH (must point at the OAuth client JSON)")
    if not settings.signature.strip():
        missing.append("SIGNATURE (your sign-off; every email must carry a real signature)")
    if not settings.resume_path or not Path(settings.resume_path).is_file():
        missing.append("RESUME_PATH (must point at an existing PDF)")
    return missing


def require_gmail_config(settings: Settings) -> list[str]:
    """The subset `gmail-auth` needs (fix round 1, F5, preserved from
    `app.cli`): only the sending address and the OAuth client-secrets file
    — authorising Gmail must not wait on the resume or the signature being
    in place."""
    from pathlib import Path

    missing = []
    if not settings.gmail_sender_address:
        missing.append("GMAIL_SENDER_ADDRESS (the Gmail account authorised by gmail-auth)")
    if not settings.gmail_client_secrets_path or not Path(settings.gmail_client_secrets_path).is_file():
        missing.append("GMAIL_CLIENT_SECRETS_PATH (must point at the OAuth client JSON)")
    return missing
