"""Task 6: `POST /api/jobs/{kind}` and `GET /api/jobs` - the browser's
only way to run a pipeline stage. Translation and status-code mapping
only, per `app/api/main.py`'s module docstring; the actual thread and
`RunJob` bookkeeping live in `app/api/jobs.py`.

The synchronous part of `POST` (raising `JobAlreadyRunning` before the
response goes out, so a double-click is rejected inline) and the
background thread each get their OWN session via `get_job_session_factory`
/ `get_job_runner_factory` - never the request's `Repos.session` from
`get_repos`. Those two dependencies are what `app.api.main.create_app`
overrides for the offline test suite (mirroring `get_repos`'s own
override seam)."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import Repos, get_repos
from app.api.jobs import JOB_KINDS, _default_runner_factory, _default_session_factory, start_job
from app.api.schemas import JobOut, JobStartIn, JobStartOut
from app.application.run_job import JobAlreadyRunning, RunJob

router = APIRouter(tags=["jobs"])


def get_job_session_factory() -> Callable[[], Session]:
    return _default_session_factory


def get_job_runner_factory() -> Callable[[Session], RunJob]:
    return _default_runner_factory


@router.post("/jobs/{kind}", response_model=JobStartOut)
def start(
    kind: str,
    payload: Optional[JobStartIn] = None,
    session_factory: Callable[[], Session] = Depends(get_job_session_factory),
    runner_factory: Callable[[Session], RunJob] = Depends(get_job_runner_factory),
):
    if kind not in JOB_KINDS:
        raise HTTPException(404, f"unknown job kind: {kind}")

    created_by = payload.created_by if payload is not None else "ui"
    try:
        job_id = start_job(kind, created_by, session_factory, runner_factory)
    except JobAlreadyRunning as exc:
        return JSONResponse(status_code=409, content={"job_id": exc.job_id})
    return JobStartOut(job_id=job_id)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(repos: Repos = Depends(get_repos)) -> list[JobOut]:
    return [
        JobOut(
            id=j.id,
            kind=j.kind,
            status=j.status,
            created_by=j.created_by,
            started_at=j.started_at,
            finished_at=j.finished_at,
            done=j.done,
            total=j.total,
            summary=j.summary,
            error=j.error,
        )
        for j in repos.job.list_recent()
    ]
