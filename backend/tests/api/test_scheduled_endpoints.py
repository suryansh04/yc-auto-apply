"""The scheduled task calls the same endpoints the UI buttons call —
one code path per stage (spec A1). `scripts/schedule-tasks.ps1` POSTs
these two kinds with `created_by: "schedule"`; these tests pin that the
API accepts exactly that shape, rejects a made-up `created_by`, and
still returns a proper 409 (with the running job's id) if the schedule
fires while the UI already has the same kind running."""


def test_poll_replies_is_startable_as_a_job(client, job_probe):
    assert client.post("/api/jobs/poll-replies").status_code == 200


def test_schedule_follow_ups_is_startable_as_a_job(client, job_probe):
    assert client.post("/api/jobs/schedule-follow-ups").status_code == 200


def test_a_scheduled_start_is_recorded_as_created_by_schedule(client, job_probe):
    client.post("/api/jobs/poll-replies", json={"created_by": "schedule"})
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "poll-replies")
    assert row["created_by"] == "schedule"


def test_schedule_follow_ups_also_honours_created_by_schedule(client, job_probe):
    client.post("/api/jobs/schedule-follow-ups", json={"created_by": "schedule"})
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "schedule-follow-ups")
    assert row["created_by"] == "schedule"


def test_an_unknown_created_by_value_is_rejected(client):
    r = client.post("/api/jobs/poll-replies", json={"created_by": "cron"})
    assert r.status_code == 422


def test_a_scheduled_start_while_one_is_already_running_is_409_with_job_id(client, blocking_job):
    # The schedule must be safe to fire even while the UI has the same
    # job running: the loser gets 409 and the running job's id, never a
    # second job racing the first (Task 6, uq_jobs_kind_active).
    first = client.post("/api/jobs/poll-replies").json()["job_id"]
    r = client.post("/api/jobs/poll-replies", json={"created_by": "schedule"})
    assert r.status_code == 409
    assert r.json()["job_id"] == first
    blocking_job.release()
