"""Jobs are the only way the UI runs a stage. The thread is real; the
stage function is a fake, so no network or DB is involved."""
import threading

import pytest


def test_starting_a_job_returns_its_id_and_runs_the_stage(client, job_probe):
    body = client.post("/api/jobs/sync").json()
    assert body["job_id"]
    job_probe.finished.wait(timeout=5)
    assert job_probe.ran == ["sync"]


def test_a_second_start_of_the_same_kind_is_409_with_the_running_id(client, blocking_job):
    first = client.post("/api/jobs/scrape").json()["job_id"]
    r = client.post("/api/jobs/scrape")
    assert r.status_code == 409
    assert r.json()["job_id"] == first
    blocking_job.release()


def test_an_unknown_kind_is_404(client):
    assert client.post("/api/jobs/nonsense").status_code == 404


def test_job_list_shows_progress_while_running(client, blocking_job):
    client.post("/api/jobs/scrape")
    blocking_job.report(3, 10)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "scrape")
    assert (row["done"], row["total"], row["status"]) == (3, 10, "running")
    blocking_job.release()


def test_a_failing_stage_records_the_type_name_only(client, failing_job):
    client.post("/api/jobs/resolve")
    failing_job.finished.wait(timeout=5)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "resolve")
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError"
    assert "secret" not in (row["error"] or "")


def test_a_failed_job_frees_the_kind_for_a_retry(client, failing_job):
    client.post("/api/jobs/resolve")
    failing_job.finished.wait(timeout=5)
    assert client.post("/api/jobs/resolve").status_code == 200


def test_the_job_thread_never_uses_the_request_session(client, job_probe):
    client.post("/api/jobs/sync")
    job_probe.finished.wait(timeout=5)
    assert job_probe.sessions_opened == 1     # its own, not the request's

    # Fix round 1 (review M1): the count above passes even if the thread
    # secretly reused the request's session - it only counts how many
    # times a stage ran, always 1 regardless. Assert real session
    # IDENTITY instead: exactly two sessions are opened per job (one for
    # `RunJob.start`'s synchronous bookkeeping, one for the thread), and
    # the stage must have run on the SECOND one, never the first.
    assert job_probe.recorder.opened == [1, 2]
    assert job_probe.stage_session_ids == [2]
    assert job_probe.stage_session_ids[0] != job_probe.recorder.opened[0]


def test_a_successful_job_reaches_succeeded_with_its_summary(client, job_probe):
    """Fix round 1 (review M13): nothing previously asserted a job ever
    reaches `succeeded` - a regression that left every finished job
    `running` forever (permanently blocking a retry, with no visible
    error) passed the whole suite."""
    client.post("/api/jobs/sync")
    job_probe.finished.wait(timeout=5)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "sync")
    assert row["status"] == "succeeded"
    assert row["summary"] == "sync ran"


def test_created_by_defaults_to_ui_and_honours_an_explicit_value(client, job_probe):
    """Fix round 1 (review M11, ruling PF-2): the `"ui"` default was only
    checked manually, not in the test suite."""
    client.post("/api/jobs/sync")
    job_probe.finished.wait(timeout=5)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "sync")
    assert row["created_by"] == "ui"


def test_created_by_honours_an_explicit_schedule_value(client, job_probe):
    client.post("/api/jobs/scrape", json={"created_by": "schedule"})
    job_probe.finished.wait(timeout=5)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "scrape")
    assert row["created_by"] == "schedule"


def test_every_session_opened_is_closed_on_the_success_path(client, job_probe):
    """Fix round 1 (review M8/M12): the sync bookkeeping session and the
    thread's own session must each be closed, not just opened."""
    client.post("/api/jobs/sync")
    job_probe.finished.wait(timeout=5)
    assert job_probe.recorder.all_closed()


def test_every_session_opened_is_closed_on_the_409_path(client, blocking_job):
    """The loser of a double-click never runs a stage, but its
    synchronous bookkeeping session must still be closed (fix round 1,
    M8/M12)."""
    client.post("/api/jobs/scrape")
    client.post("/api/jobs/scrape")           # the 409'd loser
    blocking_job.release()
    # give the winner's own thread a moment to finish and close its session
    import time
    for _ in range(50):
        if blocking_job.recorder.all_closed():
            break
        time.sleep(0.05)
    assert blocking_job.recorder.all_closed()


def test_every_session_opened_is_closed_on_the_failure_path(client, failing_job):
    client.post("/api/jobs/resolve")
    failing_job.finished.wait(timeout=5)
    assert failing_job.recorder.all_closed()


def test_a_poisoned_session_is_rolled_back_before_recording_the_failure(client, poisoning_job):
    """The serious finding: a stage that flushes a bad row, then raises,
    used to make `fail()` itself raise (SQLAlchemy's PendingRollbackError
    after an un-rolled-back failed flush) - leaving the job stuck
    `running` forever and risking the original exception's message
    escaping the thread. With `own_session.rollback()` before `fail()`,
    the job must end `failed`, record only the type name, and free the
    kind for a retry."""
    client.post("/api/jobs/sync")
    poisoning_job.finished.wait(timeout=5)

    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "sync")
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError"
    assert "secret" not in (row["error"] or "")
    assert "api_key" not in (row["error"] or "")

    # The kind is free again - a stuck `running` row would 409 this.
    assert client.post("/api/jobs/sync").status_code == 200


def test_a_racing_integrity_error_is_translated_to_409_not_500(client, racing_job):
    """Ruling T6-4: `RunJob.start` is check-then-insert with no lock, so
    two concurrent requests can both pass the guard and race the
    database's partial unique index. The loser must still see 409 with
    the winning job's id, never a raw 500 from an uncaught
    `IntegrityError`."""
    r = client.post("/api/jobs/scrape")
    assert r.status_code == 409
    assert r.json()["job_id"] == racing_job.id


def test_startup_marks_every_active_job_interrupted_regardless_of_age(all_active_jobs_client):
    """T6-1: a `queued` row with no `started_at` and a `running` row
    started only an instant ago must BOTH be recovered - the old
    3600-second age cutoff (and a `started_at is None` guard) could never
    touch either shape."""
    rows = {j["kind"]: j for j in all_active_jobs_client.get("/api/jobs").json()}
    assert rows["draft"]["status"] == "failed" and rows["draft"]["error"] == "interrupted"
    assert rows["scrape"]["status"] == "failed" and rows["scrape"]["error"] == "interrupted"


def test_startup_marks_a_job_left_running_by_a_crash_as_interrupted(stale_job_client):
    row = next(j for j in stale_job_client.get("/api/jobs").json() if j["kind"] == "scrape")
    assert row["status"] == "failed" and row["error"] == "interrupted"


def test_a_poll_replies_job_that_hits_a_dead_gmail_credential_records_gmail_auth_required(
    client, repos, monkeypatch,
):
    """Final fix wave (F2, spec §9 "Gmail 401 -> the job fails with
    're-run gmail-auth'"): the wire contract the Pipeline banner keys on
    is `status == "failed"` and `error == "GmailAuthRequired"` on the
    latest poll-replies job - the exception's TYPE NAME, never its
    message (which is the only thing the runner ever records)."""
    import threading

    from app.api import jobs as jobs_impl
    from app.ports import GmailAuthRequired
    from tests.conftest import _signalling_runner_factory

    finished = threading.Event()

    def _stage(session, progress):
        raise GmailAuthRequired("history", status=401, reason="invalid_grant")

    monkeypatch.setattr(jobs_impl, "JOB_KINDS", {**jobs_impl.JOB_KINDS, "poll-replies": _stage})
    client.app.state.job_runner_factory = _signalling_runner_factory(repos, finished)

    assert client.post("/api/jobs/poll-replies").status_code == 200
    assert finished.wait(timeout=5)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "poll-replies")
    assert row["status"] == "failed"
    assert row["error"] == "GmailAuthRequired"


def test_jobs_and_stats_render_a_job_through_the_same_mapper(client, repos):
    """Final fix wave (F8): `GET /api/jobs` and `GET /api/stats` used to
    carry two identical ten-field `JobOut(...)` translations. Now both go
    through `app.api.schemas.to_job_out`; the two listings of the same
    row must be field-for-field identical."""
    from datetime import datetime, timezone

    repos.job.upsert(
        kind="poll-replies", status="failed", created_by="schedule",
        started_at=datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 16, 9, 0, 5, tzinfo=timezone.utc),
        done=0, total=None, summary=None, error="GmailAuthRequired",
    )
    from_jobs = client.get("/api/jobs").json()
    from_stats = client.get("/api/stats").json()["jobs"]
    assert from_jobs == from_stats
    assert from_jobs[0]["error"] == "GmailAuthRequired" and from_jobs[0]["created_by"] == "schedule"


def test_to_job_out_reads_every_field_explicitly():
    """A row missing a column is a bug, not a default: `to_job_out` must
    raise rather than silently emit `None` for it."""
    from types import SimpleNamespace

    import pytest

    from app.api.schemas import to_job_out

    complete = dict(id=1, kind="sync", status="succeeded", created_by="ui", started_at=None,
                    finished_at=None, done=3, total=3, summary="synced 3", error=None)
    assert to_job_out(SimpleNamespace(**complete)).model_dump() == complete
    incomplete = {k: v for k, v in complete.items() if k != "summary"}
    with pytest.raises(AttributeError):
        to_job_out(SimpleNamespace(**incomplete))
