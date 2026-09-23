

def test_send_can_target_one_letter(client, repos, pending_and_approved, fake_sender):
    _, approved = pending_and_approved
    r = client.post("/api/send", json={"outreach_id": approved.id})
    assert r.status_code == 200
    assert r.json()["sent"] == 1
    assert len(fake_sender.sent) == 1


def test_an_unreadable_resume_is_a_400_with_the_reason(client, repos, approved_draft,
                                                       fake_sender, monkeypatch):
    """2026-09-23: the run refuses before reserving anything, and the
    refusal is a sentence rather than a 500 - the fix is the operator's
    and the screen is where he is standing."""
    from app.config import get_operator_settings

    monkeypatch.setenv("RESUME_PATH", "/nope/resume.pdf")
    get_operator_settings.cache_clear()
    r = client.post("/api/send", json={})
    get_operator_settings.cache_clear()

    assert r.status_code == 400
    assert "RESUME_PATH" in r.json()["detail"]
    assert fake_sender.sent == []
