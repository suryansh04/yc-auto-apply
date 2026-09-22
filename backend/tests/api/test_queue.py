"""The review queue over HTTP. Same use case the CLI drives, so the two
reviewers can never disagree about what is approvable."""
from app.domain.compose import compose_wire_text, first_name
from app.domain.states import OutreachState as S


def test_queue_lists_pending_drafts_with_the_composed_preview(client, repos, pending_draft):
    items = client.get("/api/queue").json()
    assert len(items) == 1
    item = items[0]
    assert item["outreach_id"] == pending_draft.outreach.id
    assert item["to"] == pending_draft.founder.resolved_email
    # The preview is what will actually be sent, not the bare stored body.
    assert item["wire_text"] == compose_wire_text(
        first_name(pending_draft.founder.full_name), item["body"], ""
    )
    assert item["word_count"] == len(item["body"].split())


def test_queue_exposes_the_cited_source_so_a_claim_is_checkable(client, pending_draft):
    item = client.get("/api/queue").json()[0]
    assert item["source_kind"] == "news"
    assert item["source_text"]


def test_queue_is_ordered_by_rank_key(client, repos, two_pending_drafts):
    ids = [i["outreach_id"] for i in client.get("/api/queue").json()]
    assert ids == [d.outreach.id for d in two_pending_drafts]


def test_approve_moves_the_row_and_stamps_approved_at(client, repos, pending_draft):
    assert client.post(f"/api/outreach/{pending_draft.outreach.id}/approve").status_code == 200
    assert repos.outreach.get(pending_draft.outreach.id).state == S.APPROVED
    assert repos.message.get_outbound(pending_draft.outreach.id, 0).approved_at is not None
    # Task 4 review, item 2: the commit boundary is this task's whole point
    # (Task 3 stopped committing on every request so a write route could
    # own it) - a successful action commits exactly once.
    assert repos.session.commit_calls == 1


def test_a_flagged_draft_can_still_be_approved(client, repos, flagged_draft):
    # 2026-09-22: this used to be a refusal. The operator's ruling: the
    # checks are advice, and the person pressing approve is the one whose
    # name is on the letter. The note is still stored and still shown; it
    # no longer overrules him.
    r = client.post(f"/api/outreach/{flagged_draft.outreach.id}/approve")
    assert r.status_code == 200
    assert repos.outreach.get(flagged_draft.outreach.id).state == S.APPROVED


def test_edit_returns_422_with_the_validators_own_messages(client, repos, pending_draft):
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/edit",
                    json={"subject": "hi", "body": "I hope this email finds you well."})
    assert r.status_code == 422
    assert any("banned" in e or "paragraph" in e or "intern" in e for e in r.json()["errors"])
    # Nothing persisted on a rejected edit.
    assert repos.message.get_outbound(pending_draft.outreach.id, 0).edited_by_user is False
    assert repos.session.commit_calls == 0


def test_a_valid_edit_persists_and_marks_it_human_written(client, repos, pending_draft, good_body):
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/edit",
                    json={"subject": "Vahan software intern", "body": good_body})
    assert r.status_code == 200
    row = repos.message.get_outbound(pending_draft.outreach.id, 0)
    assert row.edited_by_user is True and row.body == good_body
    assert repos.session.commit_calls == 1


def test_reject_discards_and_redraft_returns_it_to_drafting(client, repos, two_pending_drafts):
    a, b = two_pending_drafts
    r1 = client.post(f"/api/outreach/{a.outreach.id}/reject")
    r2 = client.post(f"/api/outreach/{b.outreach.id}/redraft")
    assert repos.outreach.get(a.outreach.id).state == S.DISCARDED
    assert repos.outreach.get(b.outreach.id).state == S.READY_TO_DRAFT
    assert r1.status_code == 200 and r2.status_code == 200
    # Each of the two successful actions commits exactly once - 2 total.
    assert repos.session.commit_calls == 2


def test_actions_on_an_unknown_outreach_are_404(client):
    assert client.post("/api/outreach/9999/approve").status_code == 404


def test_every_route_is_404_on_an_unknown_outreach_id(client, repos):
    """Task 4 review, item 1 (ruling T4-1): the brief's own test only
    covers `approve`; a reworded not-found message in
    `ReviewOutreach._outreach()` must fail every one of these, not just
    one, since `OutreachNotFound` is a type, not a string match."""
    assert client.get("/api/queue/9999").status_code == 404
    assert client.post("/api/outreach/9999/reject").status_code == 404
    assert client.post("/api/outreach/9999/redraft").status_code == 404
    r = client.post("/api/outreach/9999/edit", json={"subject": "x", "body": "y"})
    assert r.status_code == 404
    # None of the above may have committed anything.
    assert repos.session.commit_calls == 0
