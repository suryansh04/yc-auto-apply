from datetime import datetime, timedelta, timezone

import pytest

from app.application.run_job import JobAlreadyRunning, RunJob

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


class FrozenClock:
    def __init__(self, now=NOW):
        self._now = now

    def now(self):
        return self._now


class FakeJobRepo:
    def __init__(self):
        self.rows, self._next = {}, 1

    def upsert(self, **fields):
        job_id = fields.pop("id", None) or self._next
        if job_id == self._next:
            self._next += 1
        row = self.rows.setdefault(job_id, type("J", (), {"id": job_id})())
        for k, v in fields.items():
            setattr(row, k, v)
        return row

    def get(self, job_id):
        return self.rows.get(job_id)

    def active(self, kind):
        return next((r for r in self.rows.values()
                     if r.kind == kind and r.status in ("queued", "running")), None)

    def list_active(self):
        return [r for r in self.rows.values() if r.status in ("queued", "running")]


def _runner(commits=None):
    # `commit` is a 0-arg callable (RunJob's own type hint: Callable[[], None],
    # and its Step 3 sample always calls `self._commit()`), so the recorder
    # must be wrapped rather than handing `commits.append` directly — that
    # bound method needs one positional argument and raises TypeError when
    # RunJob invokes it with none.
    record = (lambda: commits.append(True)) if commits is not None else (lambda: None)
    return RunJob(FakeJobRepo(), FrozenClock(), commit=record)


def test_start_records_a_running_job():
    r = _runner()
    job_id = r.start("scrape")
    job = r.repo.get(job_id)
    assert job.kind == "scrape" and job.status == "running"
    assert job.started_at == NOW and job.created_by == "ui"


def test_a_second_start_of_the_same_kind_is_refused():
    r = _runner()
    first = r.start("scrape")
    with pytest.raises(JobAlreadyRunning) as exc:
        r.start("scrape")
    assert exc.value.job_id == first


def test_a_different_kind_may_run_concurrently():
    r = _runner()
    r.start("scrape")
    assert r.start("resolve")


def test_progress_updates_counts_and_commits():
    commits = []
    r = _runner(commits)
    job_id = r.start("draft")
    r.progress(job_id, done=3, total=10)
    job = r.repo.get(job_id)
    assert (job.done, job.total) == (3, 10)
    assert len(commits) >= 2


def test_finish_records_summary_and_frees_the_kind():
    r = _runner()
    job_id = r.start("sync")
    r.finish(job_id, "fetched 6214, matched 145")
    job = r.repo.get(job_id)
    assert job.status == "succeeded" and job.finished_at == NOW
    assert job.summary == "fetched 6214, matched 145"
    assert r.start("sync")            # the kind is free again


def test_fail_records_only_the_exception_type_name():
    r = _runner()
    job_id = r.start("resolve")
    r.fail(job_id, ValueError("key sk_live_secret is bad"))
    job = r.repo.get(job_id)
    assert job.status == "failed"
    assert job.error == "ValueError"
    assert "sk_live_secret" not in (job.error or "")


def test_recover_stale_fails_jobs_left_running_by_a_crash():
    r = _runner()
    job_id = r.start("scrape")
    r.repo.rows[job_id].started_at = NOW - timedelta(hours=3)
    recovered = r.recover_stale(max_age_seconds=3600)
    assert recovered == 1
    assert r.repo.get(job_id).status == "failed"
    assert r.repo.get(job_id).error == "interrupted"
    assert r.start("scrape")          # the index is released


def test_recover_stale_leaves_a_young_job_alone():
    r = _runner()
    r.start("scrape")
    assert r.recover_stale(max_age_seconds=3600) == 0
