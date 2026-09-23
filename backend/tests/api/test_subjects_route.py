"""`POST /api/outreach/{id}/subjects` - three subject lines for one letter
(operator request 2026-09-23). Advice: writes nothing, and every way of
having nothing to offer is a 200 with a reason.
"""


def _ask(client, draft, **payload):
    return client.post(f"/api/outreach/{draft.outreach.id}/subjects", json=payload or {})


def test_it_offers_the_usable_subjects(client, pending_draft, fake_subject_writer):
    fake_subject_writer.answer = ["the voice layer Vahan built", "Re: Vahan", "an intern for Vahan"]
    body = _ask(client, pending_draft).json()
    # The fake reply is refused; the other two are offered in order.
    assert body["subjects"] == ["the voice layer Vahan built", "an intern for Vahan"]
    assert body["unavailable"] is None


def test_it_writes_nothing(client, pending_draft, fake_subject_writer):
    before = client.get(f"/api/queue/{pending_draft.outreach.id}").json()["subject"]
    fake_subject_writer.answer = ["the voice layer Vahan built"]
    _ask(client, pending_draft)
    assert client.get(f"/api/queue/{pending_draft.outreach.id}").json()["subject"] == before


def test_asking_again_avoids_the_current_subject_and_what_was_shown(
    client, pending_draft, fake_subject_writer
):
    fake_subject_writer.answer = ["the voice layer Vahan built"]
    current = client.get(f"/api/queue/{pending_draft.outreach.id}").json()["subject"]
    _ask(client, pending_draft, avoid=["something shown earlier"])
    avoid = fake_subject_writer.context["avoid"]
    assert current in avoid
    assert "something shown earlier" in avoid


def test_nothing_usable_says_so(client, pending_draft, fake_subject_writer):
    fake_subject_writer.answer = ["Internship inquiry"]
    body = _ask(client, pending_draft).json()
    assert body["subjects"] == []
    assert body["unavailable"]


def test_a_missing_key_names_the_setting(client, repos, pending_draft, monkeypatch):
    repos.subject_writer = None
    monkeypatch.setattr("app.api.routers.queue.build_subject_writer",
                        lambda settings: (_ for _ in ()).throw(RuntimeError("SARVAM_API_KEY is not set")))
    monkeypatch.setattr("app.api.routers.queue.get_settings", lambda: None)
    assert _ask(client, pending_draft).json()["unavailable"] == "SARVAM_API_KEY is not set"


def test_an_unknown_outreach_is_404(client):
    assert client.post("/api/outreach/9999/subjects").status_code == 404
