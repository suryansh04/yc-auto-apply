"""Job bookkeeping (spec §4): one row per stage run, so the UI can show
real progress and a crash is visible rather than silent.

Adapter-free by construction — the caller injects a repo, a clock and a
commit callable, exactly as `ResolveEmails` does."""

from __future__ import annotations

from datetime import timedelta
from typing import Callable, Protocol


class JobAlreadyRunning(Exception):
    def __init__(self, kind: str, job_id: int):
        self.kind = kind
        self.job_id = job_id
        super().__init__(f"a {kind} job is already running (job {job_id})")


class _JobRepo(Protocol):
    def upsert(self, **fields): ...
    def get(self, job_id: int): ...
    def active(self, kind: str): ...
    def list_active(self) -> list: ...


class RunJob:
    def __init__(self, repo: _JobRepo, clock, commit: Callable[[], None]):
        self.repo = repo
        self._clock = clock
        self._commit = commit

    def start(self, kind: str, created_by: str = "ui") -> int:
        running = self.repo.active(kind)
        if running is not None:
            raise JobAlreadyRunning(kind, running.id)
        job = self.repo.upsert(
            kind=kind, status="running", created_by=created_by,
            started_at=self._clock.now(), done=0, total=None,
            summary=None, error=None, finished_at=None,
        )
        self._commit()
        return job.id

    def progress(self, job_id: int, done: int, total: int | None = None) -> None:
        fields = {"id": job_id, "done": done}
        if total is not None:
            fields["total"] = total
        self.repo.upsert(**fields)
        self._commit()

    def finish(self, job_id: int, summary: str = "") -> None:
        self.repo.upsert(id=job_id, status="succeeded",
                         finished_at=self._clock.now(), summary=summary[:500] or None)
        self._commit()

    def fail(self, job_id: int, exc: BaseException) -> None:
        # Type name only: an exception message can carry an API key.
        self.repo.upsert(id=job_id, status="failed",
                         finished_at=self._clock.now(), error=type(exc).__name__)
        self._commit()

    def recover_stale(self, max_age_seconds: int | None = 3600) -> int:
        """A job left `running` (or `queued`) by a crashed API holds the
        partial unique index forever. Called at startup.

        `max_age_seconds` filters by age as before when given an int. Pass
        `None` to recover EVERY active row regardless of age — including a
        `queued` row that never got a `started_at` at all, which an age
        filter can never match no matter how small (fix round 1, T6-2):
        a process crash can leave a job active at any age, and a fresh
        boot is the one moment it is safe to assume nothing is still
        running it."""
        cutoff = None if max_age_seconds is None else self._clock.now() - timedelta(seconds=max_age_seconds)
        recovered = 0
        for job in self.repo.list_active():
            started = getattr(job, "started_at", None)
            stale = cutoff is None or (started is not None and started < cutoff)
            if stale:
                self.repo.upsert(id=job.id, status="failed",
                                 finished_at=self._clock.now(), error="interrupted")
                recovered += 1
        if recovered:
            self._commit()
        return recovered
