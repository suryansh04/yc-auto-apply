"""The manual-email queue over HTTP - the browser peer of `app.cli`'s
`list-manual` / `set-email`. Same `ResolveManualEmail` use case."""


def test_manual_lists_founders_with_the_candidates_already_tried(client, manual_founder):
    item = client.get("/api/manual").json()[0]
    assert item["outreach_id"] == manual_founder.outreach.id
    assert item["candidates"][0]["verdict"] == "catch_all"


def test_setting_an_address_moves_the_row_to_drafting(client, repos, manual_founder):
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email",
                    json={"address": "Pratish@OneFin.in"})
    assert r.status_code == 200
    row = repos.outreach.get(manual_founder.outreach.id)
    assert row.state.value == "ready_to_draft"
    assert row.resolved_email == "pratish@onefin.in"     # normalised


def test_a_malformed_address_is_422_and_changes_nothing(client, repos, manual_founder):
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email", json={"address": "not-an-email"})
    assert r.status_code == 422
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "needs_manual_email"


def test_giving_up_marks_it_unresolved(client, repos, manual_founder):
    client.post(f"/api/outreach/{manual_founder.outreach.id}/email", json={"give_up": True})
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "unresolved"
