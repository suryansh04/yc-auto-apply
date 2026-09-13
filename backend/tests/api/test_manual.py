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
    # Fix round 1 (review item 2): a successful action commits exactly once.
    assert repos.session.commit_calls == 1


def test_a_malformed_address_is_422_and_changes_nothing(client, repos, manual_founder):
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email", json={"address": "not-an-email"})
    assert r.status_code == 422
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "needs_manual_email"
    # Fix round 1 (review item 2): a refusal commits nothing.
    assert repos.session.commit_calls == 0


def test_giving_up_marks_it_unresolved(client, repos, manual_founder):
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email", json={"give_up": True})
    assert r.status_code == 200
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "unresolved"
    # Fix round 1 (review item 2): a successful action commits exactly once.
    assert repos.session.commit_calls == 1


def test_an_action_on_a_founder_not_in_needs_manual_email_commits_nothing(client, repos, pending_draft):
    # Fix round 1 (review item 2): `ResolveManualEmail._manual_outreach`
    # refuses any outreach not in NEEDS_MANUAL_EMAIL (409) - nothing must be
    # committed on that refusal either, not just on a malformed address.
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/email",
                    json={"address": "someone@example.com"})
    assert r.status_code == 409
    assert repos.session.commit_calls == 0


def test_both_address_and_give_up_is_422_and_changes_nothing(client, repos, manual_founder):
    # Fix round 1 (review item 3): dropping the either-or guard must fail
    # this test - supplying both is refused, not silently resolved one way.
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email",
                    json={"address": "someone@example.com", "give_up": True})
    assert r.status_code == 422
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "needs_manual_email"
    assert repos.session.commit_calls == 0


def test_neither_address_nor_give_up_is_422_and_changes_nothing(client, repos, manual_founder):
    # Fix round 1 (review item 3): the same guard, other direction -
    # supplying neither must also be refused, not treated as a no-op give-up.
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email", json={})
    assert r.status_code == 422
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "needs_manual_email"
    assert repos.session.commit_calls == 0
