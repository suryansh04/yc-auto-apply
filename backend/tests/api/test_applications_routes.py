"""`GET /api/applications` - every application by what became of it
(operator request, 2026-09-22).

The Founders screen already filters by raw STATE. This asks a different
question: `sent`, `replied`, `closed` and `bounced` are four states and
one outcome, and `pending_approval` is one state and two outcomes
depending on whether the operator passed over it.
"""
from app.api.routers.applications import outcome_of
from app.domain.states import OutreachState as S


class _Row:
    def __init__(self, state, skipped_at=None):
        self.state = state
        self.skipped_at = skipped_at


def test_every_state_that_left_the_building_is_one_outcome():
    """A bounced letter was still sent; what happened afterwards is the
    thread's story, not this page's."""
    for state in (S.SENT, S.REPLIED, S.CLOSED, S.BOUNCED):
        assert outcome_of(_Row(state)) == "sent"


def test_a_draft_the_operator_killed_is_rejected():
    assert outcome_of(_Row(S.DISCARDED)) == "rejected"


def test_a_skipped_draft_is_told_apart_from_one_not_yet_reached():
    # The whole reason skipped_at exists: before it, these were identical.
    assert outcome_of(_Row(S.PENDING_APPROVAL)) == "waiting"
    assert outcome_of(_Row(S.PENDING_APPROVAL, skipped_at="2026-09-22T00:00:00Z")) == "skipped"


def test_an_approved_letter_is_waiting_not_skipped():
    """A skipped letter the operator later approved is waiting to send."""
    assert outcome_of(_Row(S.APPROVED, skipped_at="2026-09-22T00:00:00Z")) == "waiting"


def test_everything_before_a_draft_exists_is_not_started():
    for state in (S.NEW, S.NEEDS_EMAIL, S.NEEDS_MANUAL_EMAIL, S.READY_TO_DRAFT,
                  S.UNRESOLVED, S.FAILED):
        assert outcome_of(_Row(state)) == "not_started"


def test_the_route_lists_every_application_with_its_outcome(client, one_pending, one_approved):
    rows = client.get("/api/applications").json()
    assert {r["outreach_id"] for r in rows} == {one_pending.id, one_approved.id}
    assert {r["outcome"] for r in rows} == {"waiting"}
    # The raw state comes along, so the bucket never hides the truth.
    assert {r["state"] for r in rows} == {"pending_approval", "approved"}


def test_the_route_filters_to_one_outcome(client, repos, one_pending, one_approved):
    repos.outreach.get(one_pending.id).state = S.DISCARDED
    rows = client.get("/api/applications?outcome=rejected").json()
    assert [r["outreach_id"] for r in rows] == [one_pending.id]
    assert client.get("/api/applications?outcome=sent").json() == []


def test_the_route_reports_a_skipped_draft(client, repos, one_pending):
    from datetime import datetime, timezone

    repos.outreach.get(one_pending.id).skipped_at = datetime(
        2026, 9, 22, 10, 0, tzinfo=timezone.utc
    )
    (row,) = client.get("/api/applications?outcome=skipped").json()
    assert row["outreach_id"] == one_pending.id
    assert row["skipped_at"] is not None


def test_an_unknown_outcome_matches_nothing_rather_than_erroring(client, one_pending):
    assert client.get("/api/applications?outcome=nonsense").json() == []
