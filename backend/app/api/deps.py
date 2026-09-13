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

from app.config import get_settings

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
