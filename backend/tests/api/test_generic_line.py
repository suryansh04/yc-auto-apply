"""`POST /api/outreach/{id}/generic-line` - the fallback company line.

Operator request (2026-09-23): "give option for this to make a generic if
i am not able to find good for this specifically line". Some companies
have nothing public worth a specific sentence; the realistic alternative
to a generic line is a fabricated one, which is the failure
`app.domain.letter` exists to prevent.
"""

from app.domain import letter


def _assembled(repos, draft, insight="You did not build another job portal. You built a voice layer."):
    """Give this draft the assembled shape, with a specific company line."""
    row = repos.message.get_outbound(draft.outreach.id, 0)
    body = letter.assemble_body("Vahan", [insight])
    repos.message.upsert(id=row.id, body=body)
    return body


def test_the_specific_line_is_replaced_by_the_generic_one(client, repos, pending_draft):
    _assembled(repos, pending_draft)
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/generic-line")
    assert r.status_code == 200

    body = client.get(f"/api/queue/{pending_draft.outreach.id}").json()["body"]
    assert "You did not build another job portal." not in body
    assert letter.generic_insight("Vahan") in body


def test_everything_else_in_the_letter_survives(client, repos, pending_draft):
    _assembled(repos, pending_draft)
    client.post(f"/api/outreach/{pending_draft.outreach.id}/generic-line")
    body = client.get(f"/api/queue/{pending_draft.outreach.id}").json()["body"]
    for block in letter.fixed_blocks("Vahan"):
        assert block in body


def test_the_checks_come_back_as_advice_not_as_a_refusal(client, repos, pending_draft):
    _assembled(repos, pending_draft)
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/generic-line")
    # Same contract as `edit`: 200 with the notes in the body, whatever
    # the checks make of the generic line.
    assert r.status_code == 200
    assert "notes" in r.json() and "highlights" in r.json()


def test_a_letter_without_the_assembled_shape_is_refused_rather_than_guessed_at(
    client, repos, pending_draft
):
    # Guessing which paragraph is "the company one" in a letter that does
    # not have this shape would put the generic line in the wrong place.
    row = repos.message.get_outbound(pending_draft.outreach.id, 0)
    repos.message.upsert(id=row.id, body="A letter written some other way entirely.")
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/generic-line")
    assert r.status_code == 409
    assert "edit it by hand" in r.json()["detail"]
    # And nothing was written.
    assert client.get(f"/api/queue/{pending_draft.outreach.id}").json()["body"] == (
        "A letter written some other way entirely."
    )


def test_no_such_outreach_is_404(client):
    assert client.post("/api/outreach/9999/generic-line").status_code == 404


def test_an_approved_letter_can_be_made_generic_too(client, repos, approved_draft):
    # The same reason `edit` reaches APPROVED letters: waiting to send is
    # exactly when the weak line gets noticed.
    _assembled(repos, approved_draft)
    r = client.post(f"/api/outreach/{approved_draft.outreach.id}/generic-line")
    assert r.status_code == 200
    assert repos.outreach.get(approved_draft.outreach.id).state.value == "approved"


def test_the_queue_carries_the_generic_line_for_a_preview(client, repos, pending_draft):
    # Operator request (2026-09-23): hovering the button shows the line
    # before he commits to the swap. It comes from the API rather than
    # from the JavaScript so the preview and what the swap actually
    # writes cannot drift apart.
    _assembled(repos, pending_draft)
    item = client.get(f"/api/queue/{pending_draft.outreach.id}").json()
    assert item["generic_line"] == letter.generic_insight("Vahan")


def test_no_preview_is_offered_for_a_letter_that_cannot_be_swapped(client, repos, pending_draft):
    # `use_generic_line` refuses a letter without the assembled shape, so
    # the button must not promise what the backend will decline.
    row = repos.message.get_outbound(pending_draft.outreach.id, 0)
    repos.message.upsert(id=row.id, body="A letter written some other way entirely.")
    item = client.get(f"/api/queue/{pending_draft.outreach.id}").json()
    assert item["generic_line"] is None
