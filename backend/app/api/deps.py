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
    # Task 10: the `settings` table's `followup_day_1`/`followup_day_2` -
    # `app.api.routers.thread` reads it (via `ClassifyRepos.settings`) for
    # T9-1's next_action_at restore. Defaults to `None` for every call site
    # built before Task 10; `ClassifyReply` already falls back cleanly when
    # it is.
    settings: Any = None
    # The stored Gmail refresh token (`gmail_credentials`). The Gmail
    # connect flow both reads it (is this account authorised?) and writes
    # it (consent came back), so it belongs on the bundle like every
    # other repo rather than being constructed inside the router.
    credential: Any = None
    # Task 5: a pre-built `MailSender`, present only when a caller injected
    # one (the test harness's `fake_sender` fixture). `None` in production -
    # `app.api.routers.send` builds a real `GmailSender` itself, gated by
    # `require_send_config` below, the one time it is actually needed. This
    # is the seam that makes a real Gmail send structurally impossible from
    # the offline test suite: nothing here ever builds a real sender.
    sender: Any = None
    # A pre-built `Reviser` (the rewrite behind each highlight on the
    # review screen), present only when a caller injected one - the test
    # harness's fake. `None` in production: `app.api.routers.queue` builds
    # a real one itself, the one time it is actually needed. Same seam as
    # `sender` above, and it exists for the same reason: nothing in the
    # offline suite can ever reach a real provider.
    reviser: Any = None
    # A pre-built `ResumeParser`, present only when a caller injected one.
    # Same seam and same reason as `sender`/`reviser` above: nothing in
    # the offline suite can reach a real provider.
    parser: Any = None


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
    GmailCredentialRepo,
        JobPostingRepo,
        JobRepo,
        MessageRepo,
        OutreachRepo,
        ResumeProfileRepo,
        SettingsRepo,
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
            settings=SettingsRepo(session),
            credential=GmailCredentialRepo(session),
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


def build_critic(settings: Settings):
    """Task 13 (D22): the identical LLM-provider switch as `build_drafter`/
    `build_classifier` above, for a `Critic` adapter (Sarvam or Gemini)
    instead of a `Drafter`/`ReplyClassifier` — kept beside them, not
    duplicated a sixth time, for the same reason those two already give
    (T6-6). The critic is advisory only (D22): a caller that fails to
    build one (missing key) should simply not pass a critic to
    `DraftOutreach` rather than treat this as fatal the way `build_drafter`
    is — `app.cli._critic`/the job runner decide that, not this function."""
    if settings.llm_provider == "sarvam":
        from app.adapters import sarvam

        if not settings.sarvam_api_key:
            raise RuntimeError("SARVAM_API_KEY is not set")
        return sarvam.SarvamCritic(settings.sarvam_api_key, model=settings.sarvam_model)

    from google import genai

    from app.adapters.gemini import GeminiCritic

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=settings.gemini_api_key)
    return GeminiCritic(client, model=settings.gemini_model)


def build_resume_parser(settings: Settings):
    """The same provider switch as `build_drafter` and friends, for a
    `ResumeParser` - the adapter that turns the operator's own resume into
    citable claims.

    Extracted on 2026-09-23 from `app.cli._resume_parser`, when parsing
    stopped being CLI-only: `app.api.routers.resume` needs the same
    adapter, and `app/api/` may not import `app.cli`. Raises a plain
    `RuntimeError` naming the missing SETTING, never its value, exactly
    as the others do."""
    if settings.llm_provider == "sarvam":
        from app.adapters import sarvam

        if not settings.sarvam_api_key:
            raise RuntimeError("SARVAM_API_KEY is not set")
        return sarvam.SarvamResumeParser(settings.sarvam_api_key, model=settings.sarvam_model)

    from google import genai

    from app.adapters.gemini import GeminiResumeParser

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=settings.gemini_api_key)
    return GeminiResumeParser(client, model=settings.gemini_model)


def build_reviser(settings: Settings):
    """The same LLM-provider switch as `build_drafter`/`build_classifier`/
    `build_critic` above, for a `Reviser` adapter - the rewrite behind each
    amber highlight on the review screen. Kept beside them rather than
    duplicated a seventh time, for the reason `build_drafter`'s docstring
    gives (T6-6). Advisory like the critic: a caller that cannot build one
    (missing key) should show the checks without rewrites rather than fail
    the request."""
    if settings.llm_provider == "sarvam":
        from app.adapters import sarvam

        if not settings.sarvam_api_key:
            raise RuntimeError("SARVAM_API_KEY is not set")
        return sarvam.SarvamReviser(settings.sarvam_api_key, model=settings.sarvam_model)

    from google import genai

    from app.adapters.gemini import GeminiReviser

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=settings.gemini_api_key)
    return GeminiReviser(client, model=settings.gemini_model)


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


def load_oauth_client(path: str) -> tuple[str, str]:
    """Task 15a: extracted from `app.cli._load_oauth_client` so
    `app.api.jobs._poll_replies` can load the OAuth client id/secret a
    `GmailReader` needs without `app.api/` importing `app.cli`
    (`test_architecture.py` forbids that backwards coupling — `cli.py` is
    the operator adapter, not a library for the API layer, mirrored from
    the same extraction already applied to `on_gemini_free_tier`/
    `build_drafter`/`build_classifier`/`build_critic`/
    `require_send_config`/`require_gmail_config` above).

    `client_id` / `client_secret` from a Google Cloud OAuth client JSON
    (`{"installed": {...}}` for a Desktop app, or `{"web": {...}}`).
    Raises a plain `RuntimeError` (never a `typer`-specific exception,
    same discipline as `build_drafter`) naming what is wrong — never the
    file's contents. `app.cli._load_oauth_client` now calls this and
    translates the error into `typer.BadParameter` for its own nice CLI
    formatting."""
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    block = data.get("installed") or data.get("web") or {}
    client_id, client_secret = block.get("client_id"), block.get("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError("client secrets JSON has no installed/web client_id + client_secret")
    return client_id, client_secret


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
