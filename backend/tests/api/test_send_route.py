

def test_send_can_target_one_letter(client, repos, pending_and_approved, fake_sender):
    _, approved = pending_and_approved
    r = client.post("/api/send", json={"outreach_id": approved.id})
    assert r.status_code == 200
    assert r.json()["sent"] == 1
    assert len(fake_sender.sent) == 1
