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


def test_an_operator_facing_message_is_shown_in_full():
    """2026-09-23: a stage that has decided its message is FOR the
    operator gets to say it. Showing `failed: ValueError` while the
    sentence telling him what to do sat in a terminal was the thing that
    made a brand-new user's first Draft a dead end."""
    from app.application.errors import OperatorError

    r = _runner()
    job_id = r.start("draft")
    r.fail(job_id, OperatorError("no resume claims yet - open My resume and press Re-read"))
    assert r.repo.get(job_id).error == (
        "no resume claims yet - open My resume and press Re-read"
    )


def test_a_subclass_of_ValueError_is_not_enough_to_be_shown():
    """The rule is "written to be shown", not "looks harmless". A plain
    ValueError carrying a provider's response is still type-name only,
    and `OperatorError` subclassing ValueError must not weaken that."""
    r = _runner()
    job_id = r.start("draft")
    r.fail(job_id, ValueError("401 from provider: key sk_live_secret is bad"))
    job = r.repo.get(job_id)
    assert job.error == "ValueError"
    assert "sk_live_secret" not in (job.error or "")


def test_the_gmail_auth_marker_is_still_a_type_name():
    """The Pipeline screen matches `job.error === 'GmailAuthRequired'` to
    offer a reconnect. Turning that one into a sentence would break the
    only place a job error is acted on rather than read."""
    from app.ports import GmailAuthRequired

    r = _runner()
    job_id = r.start("poll-replies")
    r.fail(job_id, GmailAuthRequired("gmail", reason="whatever"))
    assert r.repo.get(job_id).error == "GmailAuthRequired"


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


# --- Fix round 1 (T6-2 / review Q5): recover_stale(None) -----------------
#
# Task 6's startup hook needs every active row interrupted, not just old
# ones (a crash-and-restart within the age window used to leave the kind
# 409-blocked for the rest of that window). `max_age_seconds=None` recovers
# unconditionally; `getattr(job, "started_at", None)` alone would not be
# enough since a `queued` row (never `started_at`-stamped) can't match ANY
# age filter, however small — it needs its own "no age filter at all" path.


def test_recover_stale_none_recovers_a_just_started_running_job():
    r = _runner()
    job_id = r.start("scrape")           # started_at = NOW, not stale by any age cutoff
    assert r.recover_stale(max_age_seconds=None) == 1
    job = r.repo.get(job_id)
    assert job.status == "failed" and job.error == "interrupted"
    assert r.start("scrape")             # the index is released


def test_recover_stale_none_recovers_a_queued_job_with_no_started_at():
    r = _runner()
    job_id = r.repo.upsert(kind="draft", status="queued", created_by="ui",
                           started_at=None, done=0, total=None,
                           summary=None, error=None, finished_at=None).id
    assert r.recover_stale(max_age_seconds=None) == 1
    job = r.repo.get(job_id)
    assert job.status == "failed" and job.error == "interrupted"
