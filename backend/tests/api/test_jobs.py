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


def test_startup_marks_a_job_left_running_by_a_crash_as_interrupted(stale_job_client):
    row = next(j for j in stale_job_client.get("/api/jobs").json() if j["kind"] == "scrape")
    assert row["status"] == "failed" and row["error"] == "interrupted"
