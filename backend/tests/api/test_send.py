"""`POST /api/send` over HTTP - the browser peer of `app.cli`'s `send`.
Same `SendApproved` use case; `fake_sender` (tests/conftest.py) is the only
way this route is ever reached with something other than a real Gmail
client, and it is what proves this suite can never send a real email."""


def test_send_sends_approved_rows_and_reports_counts(client, repos, approved_draft, fake_sender):
    body = client.post("/api/send", json={"limit": 5}).json()
    assert body["sent"] == 1
    assert repos.outreach.get(approved_draft.outreach.id).state.value == "sent"


def test_send_respects_the_daily_cap(client, repos, five_approved, fake_sender, monkeypatch):
    # Fix round 1 (review item 1): `sent + skipped_cap == 5` holds even with
    # no cap enforced at all - pin an explicit cap below the row count so
    # disabling the cap actually fails this test.
    monkeypatch.setenv("DAILY_SEND_CAP", "3")
    body = client.post("/api/send", json={}).json()
    assert body["sent"] == 3
    assert body["skipped_cap"] == 2


def test_send_never_sends_a_pending_draft(client, repos, pending_draft, fake_sender):
    assert client.post("/api/send", json={}).json()["sent"] == 0
    assert fake_sender.sent == []
