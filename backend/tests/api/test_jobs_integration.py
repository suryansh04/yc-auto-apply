"""Fix round 1 (review item 7): every offline job fixture in
`tests/conftest.py` shares ONE in-memory `FakeJobRepo` with
`commit=lambda: None` (or a session-bound fake) - so "progress committed
by the job thread is visible from a separate session" is true by
construction there and proves nothing about Postgres, where the thread's
session and a request's session are genuinely different connections.

This test starts a real background job (via `app.api.jobs.start_job`,
against the PRODUCTION `_default_session_factory`/`_default_runner_factory`
- a real `Session(bind=engine)` per call, a real `JobRepo`) and polls the
same row through a SEPARATE, independently-opened session, proving
read-committed visibility for real. The stage itself is a fake (no
scraping/LLM/Gmail call happens) - only the session/commit machinery is
real.

Deselected by default (`pyproject.toml`'s `addopts = "-m 'not
integration'"`). Run explicitly:

    cd backend && DATABASE_URL=postgresql+psycopg://yc:yc@localhost:5433/ycautoapply \
        python -m pytest -m integration tests/api/test_jobs_integration.py -v

`DATABASE_URL` must be exported as a real environment variable, not just
present in `backend/.env` - the root `conftest.py`'s autouse
`_ignore_operator_dotenv` fixture disables `.env` loading for every test
(so the offline suite behaves identically with or without an operator's
real `.env`), which also affects this test despite its `.env`
fallback check below (mirroring `tests/adapters/conftest.py`'s own
check, kept for parity even though it can't be satisfied that way
under pytest). `alembic upgrade head` must already have been applied -
see `tests/adapters/conftest.py`'s docstring for the same prerequisite.
SKIPS cleanly (not errors) when no `DATABASE_URL` is configured, same
convention as `tests/adapters/conftest.py`. Verified passing once against
the Postgres on :5433 named in `backend/.env` (fix round 1)."""

import os
import threading

import pytest

pytestmark = pytest.mark.integration


def _database_url_configured() -> bool:
    if os.environ.get("DATABASE_URL"):
        return True
    try:
        with open(".env", encoding="utf-8") as fh:
            return any(
                line.split("=", 1)[0].strip() == "DATABASE_URL" and line.split("=", 1)[1].strip()
                for line in fh if "=" in line and not line.lstrip().startswith("#")
            )
    except OSError:
        return False


def test_progress_committed_by_the_job_thread_is_visible_from_a_separate_session():
    if not _database_url_configured():
        pytest.skip("DATABASE_URL not set")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.adapters.db.models import Job
    from app.api import jobs as jobs_impl
    from app.config import get_settings

    engine = create_engine(get_settings().database_url)

    reported = threading.Event()
    resume = threading.Event()

    def _fake_stage(session, progress):
        progress(3, 10)
        reported.set()
        # Hold the row at "3 of 10, running" until the test has read it
        # back through its OWN, separately-opened session.
        assert resume.wait(timeout=5), "test never released the stage"
        return "integration test job"

    original_kinds = dict(jobs_impl.JOB_KINDS)
    job_id = None
    try:
        jobs_impl.JOB_KINDS = {**original_kinds, "sync": _fake_stage}

        job_id = jobs_impl.start_job(
            "sync", "ui",
            session_factory=jobs_impl._default_session_factory,
            runner_factory=jobs_impl._default_runner_factory,
        )
        assert reported.wait(timeout=5), "the job thread never reported progress"

        # The whole point: a brand-new session, opened independently of
        # the job thread's own session/connection.
        with Session(bind=engine) as outside_session:
            row = outside_session.get(Job, job_id)
            assert (row.done, row.total, row.status) == (3, 10, "running")

        resume.set()

        # Let the thread finish and commit `succeeded` before tearing
        # down, then confirm that too is visible from a third session.
        for _ in range(50):
            with Session(bind=engine) as check:
                if check.get(Job, job_id).status == "succeeded":
                    break
            import time
            time.sleep(0.1)
        with Session(bind=engine) as outside_session:
            row = outside_session.get(Job, job_id)
            assert row.status == "succeeded"
            assert row.summary == "integration test job"
    finally:
        jobs_impl.JOB_KINDS = original_kinds
        resume.set()
        if job_id is not None:
            with Session(bind=engine) as cleanup:
                row = cleanup.get(Job, job_id)
                if row is not None:
                    cleanup.delete(row)
                    cleanup.commit()
        engine.dispose()
