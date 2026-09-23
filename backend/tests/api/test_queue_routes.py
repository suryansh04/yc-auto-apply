

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


# --- the approved screen (operator request 2026-09-22) --------------------

def test_approved_lists_only_approved_letters(client, repos, one_pending, one_approved):
    ids = [r["outreach_id"] for r in client.get("/api/approved").json()]
    assert ids == [one_approved.id]
    # ...and the review queue still shows only the undecided ones.
    assert [r["outreach_id"] for r in client.get("/api/queue").json()] == [one_pending.id]


def test_approved_carries_the_whole_letter_not_just_a_row(client, one_approved):
    (item,) = client.get("/api/approved").json()
    # The composed wire text is what leaves the mailbox (ruling R2), so it
    # is what this screen must show.
    assert item["wire_text"]
    assert item["wire_text"].startswith("Hi ")
    assert item["to"] and item["subject"]


def test_approved_is_empty_when_nothing_is_approved(client, one_pending):
    assert client.get("/api/approved").json() == []


def test_unapprove_route_returns_the_letter_to_the_queue(client, repos, one_approved):
    r = client.post(f"/api/outreach/{one_approved.id}/unapprove")
    assert r.status_code == 200
    assert repos.outreach.get(one_approved.id).state == "pending_approval"
    assert [x["outreach_id"] for x in client.get("/api/approved").json()] == []
    assert [x["outreach_id"] for x in client.get("/api/queue").json()] == [one_approved.id]


def test_unapprove_route_refuses_a_letter_that_was_never_approved(client, one_pending):
    assert client.post(f"/api/outreach/{one_pending.id}/unapprove").status_code == 409


def test_check_pairs_each_note_with_the_words_it_is_about(client, one_pending):
    """Operator request (2026-09-22): mark the offending text, the way a
    writing assistant does."""
    r = client.post(f"/api/outreach/{one_pending.id}/check",
                    json={"subject": "A subject here", "body": "One thing; another thing."})
    assert r.status_code == 200
    body = r.json()
    assert [h["message"] for h in body["highlights"]] == body["notes"]
    semicolon = [h for h in body["highlights"] if "semicolon" in h["message"]]
    assert semicolon and semicolon[0]["quote"] == ";"


def test_a_note_about_the_whole_letter_carries_no_quote(client, one_pending):
    r = client.post(f"/api/outreach/{one_pending.id}/check",
                    json={"subject": "x", "body": "Too short."})
    highlights = r.json()["highlights"]
    assert any(h["quote"] is None for h in highlights)


def test_edit_also_returns_the_highlights(client, one_pending):
    r = client.post(f"/api/outreach/{one_pending.id}/edit",
                    json={"subject": "A subject here", "body": "One thing; another thing."})
    assert r.status_code == 200
    assert any(h["quote"] == ";" for h in r.json()["highlights"])
