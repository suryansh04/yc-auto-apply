"""Background stage execution (Phase 2 spec §4).

A job thread opens its OWN session: sharing the request's would keep a web
request alive for the length of a four-minute scrape and hand the same
`Session` to two threads at once. `start_job` below is the only place that
spawns that thread; everything else is orchestration around it.

Each `JOB_KINDS` entry mirrors the matching `app/cli.py` command exactly -
same repos, same use case, same commit boundary (`resolve` commits per
founder inside `ResolveEmails.run` itself; `scrape` commits per company;
`sync` and `draft` commit once at the end) - `cli.py` stays the reference
for what "correct" wiring looks like.

PF-1 (controller ruling): only the four stages that exist today are
registered. `poll-replies`, `classify-replies` and `schedule-follow-ups`
are NOT stubbed here; Tasks 8, 9 and 11 each add their own `JOB_KINDS`
entry when their use case lands.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.application.run_job import RunJob

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, Optional[int]], None]


class _UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


# --- stage functions -----------------------------------------------------
# Each takes its OWN session (never the request's) and a progress callback,
# and returns a one-line summary - exactly what `cli.py`'s echo prints.


def _sync(session: Session, progress: ProgressFn) -> str:
    from app.adapters.db.repositories import CompanyRepo
    from app.adapters.yc_oss import YCOSSDirectory
    from app.application.sync_companies import SyncCompanies

    result = SyncCompanies(YCOSSDirectory(), CompanyRepo(session)).run()
    session.commit()
    return f"fetched {result.fetched}, matched {result.matched}, upserted {result.upserted}"


def _scrape(session: Session, progress: ProgressFn) -> str:
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyNewsRepo,
        CompanyRepo,
        FounderRepo,
        JobPostingRepo,
        OutreachRepo,
    )
    from app.adapters.yc_page import YCPageSource
    from app.application.scrape_company_page import ScrapeCompanyPage, ScrapeRepos

    companies = CompanyRepo(session).list_by_status(status="Active")
    repos = ScrapeRepos(
        founder=FounderRepo(session),
        job_posting=JobPostingRepo(session),
        news=CompanyNewsRepo(session),
        launch=CompanyLaunchRepo(session),
        outreach=OutreachRepo(session),
        session=session,
    )
    use_case = ScrapeCompanyPage(YCPageSource(), repos)

    total_founders = total_new_founders = errors = 0
    progress(0, len(companies))
    for i, company in enumerate(companies, start=1):
        result = use_case.run(company)
        session.commit()  # per company (spec: preserve the CLI's commit boundary)
        total_founders += result.founders
        total_new_founders += result.new_founders
        if result.error:
            errors += 1
        progress(i, len(companies))

    return (
        f"companies {len(companies)}, founders {total_founders} "
        f"(new {total_new_founders}), errors {errors}"
    )


def _resolve(session: Session, progress: ProgressFn) -> str:
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyRepo,
        EmailCandidateRepo,
        FounderRepo,
        OutreachRepo,
    )
    from app.adapters.email_verifier import MyEmailVerifierClient
    from app.application.resolve_emails import ResolveEmails, ResolveRepos
    from app.config import get_settings

    settings = get_settings()
    repos = ResolveRepos(
        founder=FounderRepo(session),
        company=CompanyRepo(session),
        launch=CompanyLaunchRepo(session),
        outreach=OutreachRepo(session),
        email_candidate=EmailCandidateRepo(session),
        session=session,
    )
    verifier = MyEmailVerifierClient(settings.email_verifier_api_key)
    try:
        # ResolveEmails.run commits after every founder itself (ruling
        # FR-3, a paid API quota is at stake) - preserved unchanged here.
        result = ResolveEmails(verifier, repos).run()
    finally:
        verifier.close()

    progress(result.resolved + result.manual + result.errors, None)
    return (
        f"resolved {result.resolved}, manual {result.manual}, "
        f"errors {result.errors}, verifications used {result.verifications_used}, "
        f"paused on quota: {result.paused_on_quota}"
    )


def _draft(session: Session, progress: ProgressFn) -> str:
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyNewsRepo,
        CompanyRepo,
        FounderRepo,
        JobPostingRepo,
        MessageRepo,
        OutreachRepo,
        ResumeProfileRepo,
    )
    from app.application.draft_outreach import DraftOutreach, DraftRepos
    from app.cli import _drafter, _on_gemini_free_tier

    if _on_gemini_free_tier():
        # `cli.py draft` refuses the same way unless --allow-free-tier is
        # passed; the UI has no such flag, so it always refuses (fails the
        # job cleanly rather than silently sending founder data to a
        # free-tier key that may train on it, D12).
        raise RuntimeError(
            "refusing to draft on a free-tier Gemini key from the UI job runner"
        )

    drafter = _drafter()
    repos = DraftRepos(
        founder=FounderRepo(session),
        company=CompanyRepo(session),
        news=CompanyNewsRepo(session),
        launch=CompanyLaunchRepo(session),
        job_posting=JobPostingRepo(session),
        outreach=OutreachRepo(session),
        message=MessageRepo(session),
        resume_profile=ResumeProfileRepo(session),
    )
    result = DraftOutreach(drafter, repos).run()
    session.commit()  # once at the end, same as `cli.py draft`

    progress(result.drafted + result.manual_write + result.errors, None)
    summary = (
        f"drafted {result.drafted}, manual write {result.manual_write}, "
        f"retried {result.retried}, errors {result.errors}"
    )
    if result.aborted:
        summary += " - ABORTED after consecutive drafter failures"
    return summary


# PF-1: register ONLY the stages that exist today. Do not add
# poll-replies / classify-replies / schedule-follow-ups here - Tasks 8, 9
# and 11 each append their own entry when their use case lands.
JOB_KINDS: dict[str, Callable[[Session, ProgressFn], str]] = {
    "sync": _sync,
    "scrape": _scrape,
    "resolve": _resolve,
    "draft": _draft,
}


# --- default session / runner factories -----------------------------------
# The production seam: `app.api.deps.real_repos` takes an injectable
# `session_factory` (Task 3's fix round) for exactly this reason - a caller
# that needs its own session, separate from the request's, follows the
# same pattern rather than reusing `get_repos`.


def _default_session_factory() -> Session:
    from app.api.deps import _session

    return _session()


def _default_runner_factory(session: Session) -> RunJob:
    from app.adapters.db.repositories import JobRepo

    return RunJob(JobRepo(session), _UTCClock(), commit=session.commit)


# --- orchestration ----------------------------------------------------------


def start_job(
    kind: str,
    created_by: str,
    session_factory: Callable[[], Session],
    runner_factory: Callable[[Session], RunJob],
) -> int:
    """Start one stage in a background thread and return its job id.

    Two sessions are involved, never shared: a short-lived one for the
    synchronous bookkeeping (`RunJob.start`, which must raise
    `JobAlreadyRunning` before this call returns so a double click is
    rejected inline), and a second, separate one the background thread
    opens for itself and holds for the whole stage run. Raises `KeyError`
    for an unknown kind (the router turns that into 404) and
    `JobAlreadyRunning` when that kind is already queued/running (the
    router turns that into 409 with the running job's id) - the database's
    partial unique index is still the real backstop; this is only the
    inline fast path.
    """
    if kind not in JOB_KINDS:
        raise KeyError(kind)

    sync_session = session_factory()
    try:
        job_id = runner_factory(sync_session).start(kind, created_by)
    finally:
        sync_session.close()

    def _run() -> None:
        own_session = session_factory()
        own_runner = runner_factory(own_session)
        try:
            stage = JOB_KINDS[kind]
            summary = stage(
                own_session, lambda done, total=None: own_runner.progress(job_id, done, total)
            )
            own_runner.finish(job_id, summary)
        except BaseException as exc:
            # Type name only (never str(exc)): an exception message can
            # carry an API key or other secret.
            log.warning("job %s (%s) failed: %s", job_id, kind, type(exc).__name__)
            own_runner.fail(job_id, exc)
        finally:
            own_session.close()

    threading.Thread(target=_run, name=f"job-{kind}-{job_id}", daemon=True).start()
    return job_id


def recover_stale_jobs(app, max_age_seconds: int = 3600) -> int:
    """Startup hook (called from `app.api.main.create_app`): a job left
    `running` by a crashed process holds the partial unique index forever,
    so every stale `queued`/`running` row older than `max_age_seconds` is
    marked `failed`/`interrupted` once at boot, freeing its kind."""
    session_factory = app.state.job_session_factory
    runner_factory = app.state.job_runner_factory
    session = session_factory()
    try:
        return runner_factory(session).recover_stale(max_age_seconds)
    finally:
        session.close()
