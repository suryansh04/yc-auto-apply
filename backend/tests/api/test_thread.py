"""Task 10: the thread timeline (`GET /api/outreach/{id}/thread`) and hand
label correction (`POST /api/messages/{id}/label`).

The brief's own five tests are first, verbatim. Beyond them, this file
also pins:

- correcting a HUMAN label to a MACHINE one restores `next_action_at` to
  the exact right value (`sent_at + followup_day_1`), not merely
  non-null (T9-1's whole point — a wrong value here would silently
  mis-schedule the founder's next follow-up);
- the same restore path reads `followup_day_1`/`followup_day_2` from the
  `settings` table when a row is configured, not just the hardcoded
  fallback;
- a second follow-up step (sequence_step=1) restores using
  `followup_day_2`, not `followup_day_1`;
- correcting HUMAN -> HUMAN (a different human label) leaves the
  outreach's state and `next_action_at` alone entirely;
- correcting MACHINE -> MACHINE likewise leaves the state alone;
- the thread renders in time order across TWO outbound steps plus an
  inbound reply, not just one outbound + one inbound;
- a correction on an outreach already in a terminal state (`BOUNCED`)
  changes the label but never corrupts the state;
- `GET` on a missing outreach id is a 404, and a `commit_calls` check
  pins that a refused correction (422/404) never commits.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domain.states import OutreachState as S


def _thread(client, outreach_id: int):
    return client.get(f"/api/outreach/{outreach_id}/thread").json()


# --- Brief's own tests --------------------------------------------------------


def test_thread_returns_outbound_and_inbound_in_time_order(client, thread_with_reply):
    body = _thread(client, thread_with_reply.outreach.id)
    directions = [m["direction"] for m in body["messages"]]
    assert directions == ["outbound", "inbound"]


def test_thread_exposes_a_deep_link_to_gmail(client, thread_with_reply):
    body = _thread(client, thread_with_reply.outreach.id)
    assert thread_with_reply.thread_id in body["gmail_url"]


def test_a_label_can_be_corrected_by_hand(client, repos, thread_with_reply):
    reply_id = thread_with_reply.reply.id
    r = client.post(f"/api/messages/{reply_id}/label", json={"label": "REFERRAL"})
    assert r.status_code == 200
    assert repos.message.get(reply_id).label == "REFERRAL"


def test_correcting_to_a_human_label_cancels_follow_ups(client, repos, thread_with_auto_reply):
    client.post(f"/api/messages/{thread_with_auto_reply.reply.id}/label", json={"label": "INTERESTED"})
    outreach = repos.outreach.get(thread_with_auto_reply.outreach.id)
    assert outreach.state.value == "replied" and outreach.next_action_at is None


def test_an_invalid_label_is_422(client, thread_with_reply):
    r = client.post(f"/api/messages/{thread_with_reply.reply.id}/label", json={"label": "GREAT"})
    assert r.status_code == 422


# --- Beyond the brief ----------------------------------------------------------


def _human_labelled_outreach(repos, id: int, label: str = "INTERESTED", sequence_step: int = 0,
                             sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc), state: S = S.REPLIED):
    """An outreach (`REPLIED` by default; pass `state=S.BOUNCED` etc. for
    the guard-coverage tests below) whose one outbound step (at
    `sequence_step`) already went out at `sent_at`, with an inbound reply
    already carrying a human `label` (`next_action_at` cleared, exactly as
    `apply_label`'s forward path leaves it) — the base case for T9-1's
    restore path."""
    repos.company.add(id=id, domain=f"co{id}.example.com", name=f"Company {id}")
    founder = repos.founder.add(id=id, company_id=id, full_name=f"Founder {id}")
    outreach = repos.outreach.add(id=id, founder_id=founder.id, state=str(state))
    outreach.resolved_email = f"founder{id}@co{id}.example.com"
    outreach.sequence_step = sequence_step
    outreach.next_action_at = None
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=sequence_step, direction="outbound",
        subject="Backend intern", body="Would you take an intern?",
        gmail_message_id=f"sent-{id}", gmail_thread_id=f"thread-{id}",
        approved_at=sent_at, sent_at=sent_at,
    )
    reply = repos.message.upsert(
        outreach_id=outreach.id, sequence_step=sequence_step, direction="inbound",
        subject="Re: Backend intern", body="Sounds good.",
        gmail_message_id=f"reply-{id}", gmail_thread_id=f"thread-{id}",
        received_at=sent_at + timedelta(hours=1),
        label=label, confidence=0.9, rationale="operator or model call",
        classified_at=sent_at + timedelta(hours=2),
    )
    return SimpleNamespace(outreach=outreach, founder=founder, reply=reply)


def test_correcting_human_to_machine_restores_next_action_at_to_the_right_value(client, repos):
    sent_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    item = _human_labelled_outreach(repos, 500, sent_at=sent_at)

    r = client.post(f"/api/messages/{item.reply.id}/label", json={"label": "OUT_OF_OFFICE"})
    assert r.status_code == 200

    outreach = repos.outreach.get(item.outreach.id)
    assert outreach.state == S.SENT
    # T9-1: default followup_day_1 = 5, no settings row configured.
    assert outreach.next_action_at == sent_at + timedelta(days=5)


def test_the_restore_reads_followup_day_1_from_the_settings_table(client, repos):
    repos.settings.set(followup_day_1=9, followup_day_2=20)
    sent_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    item = _human_labelled_outreach(repos, 501, sent_at=sent_at)

    client.post(f"/api/messages/{item.reply.id}/label", json={"label": "AUTO_REPLY"})

    outreach = repos.outreach.get(item.outreach.id)
    assert outreach.next_action_at == sent_at + timedelta(days=9)


def test_the_restore_uses_followup_day_2_for_a_second_step(client, repos):
    repos.settings.set(followup_day_1=9, followup_day_2=20)
    sent_at = datetime(2026, 9, 6, tzinfo=timezone.utc)
    item = _human_labelled_outreach(repos, 502, sequence_step=1, sent_at=sent_at)

    client.post(f"/api/messages/{item.reply.id}/label", json={"label": "OUT_OF_OFFICE"})

    outreach = repos.outreach.get(item.outreach.id)
    assert outreach.state == S.SENT
    assert outreach.next_action_at == sent_at + timedelta(days=20)


def test_the_restore_uses_followup_day_2_for_a_step_two_correction(client, repos):
    """Task 11 review (ruling T11-1): before the fix,
    `_followup_interval_days(next_step=3)` — reached from a step-2 row
    (`outreach.sequence_step == 2`) — returned `None`, so this correction
    left `next_action_at = None` and the row could never become due for
    `ScheduleFollowUps` to close (spec §6 defines no third interval, so
    the fix reuses `followup_day_2`, mirroring `ScheduleFollowUps._
    interval_days`'s own reuse choice for the same step)."""
    sent_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    item = _human_labelled_outreach(repos, 507, sequence_step=2, sent_at=sent_at)

    r = client.post(f"/api/messages/{item.reply.id}/label", json={"label": "OUT_OF_OFFICE"})
    assert r.status_code == 200

    outreach = repos.outreach.get(item.outreach.id)
    assert outreach.state == S.SENT
    assert outreach.next_action_at == sent_at + timedelta(days=12)


def test_a_corrected_step_two_row_eventually_closes(client, repos):
    """End to end (T11-1): a step-2 SENT row mislabelled REPLIED, then
    corrected back to a machine label, must actually reach CLOSED once
    `ScheduleFollowUps` next runs past the restored `next_action_at` — not
    sit in SENT forever because the restore path left it un-schedulable."""
    from app.application.schedule_follow_ups import FollowUpRepos, ScheduleFollowUps

    sent_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    item = _human_labelled_outreach(repos, 508, sequence_step=2, sent_at=sent_at)

    r = client.post(f"/api/messages/{item.reply.id}/label", json={"label": "OUT_OF_OFFICE"})
    assert r.status_code == 200
    assert repos.outreach.get(item.outreach.id).state == S.SENT

    class _PastDueClock:
        def now(self_inner):
            return sent_at + timedelta(days=13)  # past the restored next_action_at (+12)

    follow_up_repos = FollowUpRepos(outreach=repos.outreach, message=repos.message,
                                    session=repos.session, settings=repos.settings)
    result = ScheduleFollowUps(follow_up_repos, _PastDueClock()).run()

    assert result.closed == 1
    assert repos.outreach.get(item.outreach.id).state == S.CLOSED


def test_correcting_human_to_human_leaves_the_state_alone(client, repos):
    item = _human_labelled_outreach(repos, 503, label="REJECTED")

    r = client.post(f"/api/messages/{item.reply.id}/label", json={"label": "INTERESTED"})
    assert r.status_code == 200

    outreach = repos.outreach.get(item.outreach.id)
    assert outreach.state == S.REPLIED and outreach.next_action_at is None
    assert repos.message.get(item.reply.id).label == "INTERESTED"


def test_a_human_to_machine_correction_on_a_bounced_outreach_does_not_restore_sent(client, repos):
    # Fix round 1 (review): `_restore_sent`'s own `assert_transition` guard
    # (REPLIED -> SENT is the only legal source per T9-1) had zero test
    # coverage - deleting it left every existing test passing. A `BOUNCED`
    # outreach carrying a human-labelled reply, corrected to a machine
    # label, must NOT flip to SENT: assert_transition(BOUNCED, SENT) is
    # illegal (no such edge in `app.domain.states._LEGAL`), so this must be
    # refused exactly like `_mark_replied`'s guard already is for the
    # forward direction - the label still changes, the state and
    # next_action_at do not. (Mirrors Task 9's own
    # test_a_human_label_on_an_already_bounced_outreach_never_overwrites_bounced
    # for the same class of gap.)
    item = _human_labelled_outreach(repos, 506, label="INTERESTED", state=S.BOUNCED)

    r = client.post(f"/api/messages/{item.reply.id}/label", json={"label": "AUTO_REPLY"})
    assert r.status_code == 200

    outreach = repos.outreach.get(item.outreach.id)
    assert outreach.state == S.BOUNCED
    assert outreach.next_action_at is None
    assert repos.message.get(item.reply.id).label == "AUTO_REPLY"


def test_correcting_machine_to_machine_leaves_the_state_alone(client, repos, thread_with_auto_reply):
    r = client.post(f"/api/messages/{thread_with_auto_reply.reply.id}/label", json={"label": "OUT_OF_OFFICE"})
    assert r.status_code == 200

    outreach = repos.outreach.get(thread_with_auto_reply.outreach.id)
    assert outreach.state == S.SENT and outreach.next_action_at == datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert repos.message.get(thread_with_auto_reply.reply.id).label == "OUT_OF_OFFICE"


def test_thread_timeline_orders_two_outbound_steps_and_a_reply(client, repos):
    repos.company.add(id=504, domain="co504.example.com", name="Company 504")
    founder = repos.founder.add(id=504, company_id=504, full_name="Founder 504")
    outreach = repos.outreach.add(id=504, founder_id=founder.id, state=str(S.SENT))
    outreach.resolved_email = "founder@co504.example.com"
    outreach.sequence_step = 1

    step0_sent = datetime(2026, 9, 1, tzinfo=timezone.utc)
    step1_sent = datetime(2026, 9, 6, tzinfo=timezone.utc)
    reply_received = datetime(2026, 9, 8, tzinfo=timezone.utc)

    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject="Backend intern", body="Initial email",
        gmail_message_id="sent-504-0", gmail_thread_id="thread-504",
        approved_at=step0_sent, sent_at=step0_sent,
    )
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=1, direction="outbound",
        subject="Re: Backend intern", body="Following up",
        gmail_message_id="sent-504-1", gmail_thread_id="thread-504",
        approved_at=step1_sent, sent_at=step1_sent,
    )
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=1, direction="inbound",
        subject="Re: Backend intern", body="Sorry, missed this",
        gmail_message_id="reply-504", gmail_thread_id="thread-504",
        received_at=reply_received,
    )

    body = _thread(client, outreach.id)
    ids_in_order = [m["id"] for m in body["messages"]]
    bodies_in_order = [m["body"] for m in body["messages"]]
    assert bodies_in_order == ["Initial email", "Following up", "Sorry, missed this"]
    assert len(ids_in_order) == 3


def test_a_correction_on_a_bounced_outreach_does_not_corrupt_it(client, repos):
    repos.company.add(id=505, domain="co505.example.com", name="Company 505")
    founder = repos.founder.add(id=505, company_id=505, full_name="Founder 505")
    outreach = repos.outreach.add(id=505, founder_id=founder.id, state=str(S.BOUNCED))
    outreach.resolved_email = "founder@co505.example.com"
    outreach.sequence_step = 0
    outreach.next_action_at = None
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject="Backend intern", body="Would you take an intern?",
        gmail_message_id="sent-505", gmail_thread_id="thread-505",
        approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    reply = repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="inbound",
        subject="Re: Backend intern", body="mailer-daemon text",
        gmail_message_id="reply-505", gmail_thread_id="thread-505",
        received_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        label="UNCLEAR", confidence=0.2, rationale="bounce-ish text",
    )

    r = client.post(f"/api/messages/{reply.id}/label", json={"label": "REJECTED"})
    assert r.status_code == 200

    outreach = repos.outreach.get(505)
    assert outreach.state == S.BOUNCED
    assert outreach.next_action_at is None
    assert repos.message.get(reply.id).label == "REJECTED"


def test_a_missing_outreach_is_404(client):
    r = client.get("/api/outreach/999999/thread")
    assert r.status_code == 404


def test_a_missing_message_is_404(client):
    r = client.post("/api/messages/999999/label", json={"label": "INTERESTED"})
    assert r.status_code == 404


def test_a_refused_correction_never_commits(client, repos, thread_with_reply):
    before = repos.session.commit_calls
    r = client.post(f"/api/messages/{thread_with_reply.reply.id}/label", json={"label": "GREAT"})
    assert r.status_code == 422
    assert repos.session.commit_calls == before


def test_a_successful_correction_commits_exactly_once(client, repos, thread_with_reply):
    before = repos.session.commit_calls
    r = client.post(f"/api/messages/{thread_with_reply.reply.id}/label", json={"label": "REJECTED"})
    assert r.status_code == 200
    assert repos.session.commit_calls == before + 1


def test_a_hand_corrected_message_is_flagged_corrected_in_the_thread(client, thread_with_reply):
    client.post(f"/api/messages/{thread_with_reply.reply.id}/label", json={"label": "REJECTED"})
    body = _thread(client, thread_with_reply.outreach.id)
    reply_out = next(m for m in body["messages"] if m["direction"] == "inbound")
    assert reply_out["corrected"] is True
    outbound_out = next(m for m in body["messages"] if m["direction"] == "outbound")
    assert outbound_out["corrected"] is False


# --- Final fix wave (F6): the label route rejects outbound rows -------------


def test_labelling_an_outbound_message_is_409_and_changes_nothing(client, repos, thread_with_reply):
    """`POST /messages/{id}/label` on the operator's OWN outbound row must
    be refused (409, by exception type - not by matching a message) and
    leave both the row and the parent outreach exactly as they were: an
    outbound row never carries a label, and a human label applied
    through it would otherwise move the outreach to REPLIED with no
    reply behind it."""
    outbound = next(m for m in repos.message.list_by_outreach(thread_with_reply.outreach.id)
                    if m.direction == "outbound")
    before_state = repos.outreach.get(thread_with_reply.outreach.id).state

    r = client.post(f"/api/messages/{outbound.id}/label", json={"label": "INTERESTED"})

    assert r.status_code == 409
    assert repos.message.get(outbound.id).label is None
    assert repos.outreach.get(thread_with_reply.outreach.id).state == before_state
    assert repos.session.commit_calls == 0


def test_the_outbound_refusal_is_mapped_by_type_not_by_message_text(client, monkeypatch, thread_with_reply):
    from app.application.classify_reply import ClassifyReply, NotAnInboundMessage

    def _raise(self, message, label, corrected=False, **extra):
        raise NotAnInboundMessage("nope")  # arbitrary text - the type alone decides the 409

    monkeypatch.setattr(ClassifyReply, "apply_label", _raise)
    r = client.post(f"/api/messages/{thread_with_reply.reply.id}/label", json={"label": "INTERESTED"})
    assert r.status_code == 409
    assert r.json()["detail"] == "nope"
