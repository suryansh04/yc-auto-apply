

# --- the checks are advice, not a gate (operator decision 2026-09-22) ------

def test_check_returns_the_notes_without_writing_anything(client, repos, one_pending):
    row = repos.message.get_outbound(one_pending.id, 0)
    before = (row.subject, row.body, repos.outreach.get(one_pending.id).state)
    r = client.post(f"/api/outreach/{one_pending.id}/check")
    assert r.status_code == 200
    assert isinstance(r.json()["notes"], list)
    row = repos.message.get_outbound(one_pending.id, 0)
    assert (row.subject, row.body, repos.outreach.get(one_pending.id).state) == before


def test_check_on_a_draft_the_rules_dislike_is_still_a_200(client, one_pending):
    """A refusal would make the checks a gate again. They are suggestions:
    the route always answers, and the human decides."""
    r = client.post(f"/api/outreach/{one_pending.id}/check",
                    json={"subject": "hi", "body": "I hope this email finds you well."})
    assert r.status_code == 200
    assert r.json()["notes"]


def test_check_can_judge_text_the_operator_is_still_typing(client, one_pending):
    r = client.post(f"/api/outreach/{one_pending.id}/check",
                    json={"subject": "A subject here", "body": "Body with a semicolon; in it."})
    assert r.status_code == 200
    assert any("semicolon" in n for n in r.json()["notes"])


def test_check_on_an_unknown_outreach_is_404(client):
    assert client.post("/api/outreach/999999/check").status_code == 404


def test_approving_a_draft_the_checks_dislike_still_works(client, repos, one_pending):
    """The point of the change: a note never blocks the person who signs
    the letter."""
    notes = client.post(f"/api/outreach/{one_pending.id}/check").json()["notes"]
    assert client.post(f"/api/outreach/{one_pending.id}/approve").status_code == 200
    assert repos.outreach.get(one_pending.id).state == "approved"
    assert isinstance(notes, list)
