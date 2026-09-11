"""Task 15: `SendApproved` — reserve-before-send (spec §13, ruling R1),
compose-at-send-time (ruling R2), the daily cap counted against messages
already sent today (D8/D9, spec §18), and `APPROVED -> SENT` only.

The four tests under "brief" are the task brief's, verbatim. Everything
after them pins the properties the brief's tests would let a mutant slip
past: the reservation being committed *before* the sender is called, the
rowcount-0 stop, the crash-resume path surfacing `needs_manual_check`, the
cap counting today's earlier sends, and the composed wire text.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.application.send_approved import SendApproved
from app.domain.compose import OPT_OUT_LINE
from app.domain.states import IllegalTransition
from app.domain.states import OutreachState as S
from tests.fakes import FakeSender, FrozenClock

# --- brief -------------------------------------------------------------------


def test_only_approved_messages_are_sent(repos, pending_and_approved):
    s = FakeSender()
    SendApproved(s, FrozenClock(), repos).run(cap=10)
    assert len(s.sent) == 1
    assert s.sent[0]["to"] == "approved@example.com"


def test_daily_cap_is_respected(repos, five_approved):
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=2)
    assert len(s.sent) == 2
    assert result.skipped_cap == 3


def test_crash_after_gmail_call_does_not_double_send(repos, one_approved):
    class ExplodingSender(FakeSender):
        def send(self, *a, **kw):
            super().send(*a, **kw)
            raise RuntimeError("crashed after the API call")

    s = ExplodingSender()
    with pytest.raises(RuntimeError):
        SendApproved(s, FrozenClock(), repos).run(cap=10)

    # The reservation survives, so the retry cannot send a second copy.
    s2 = FakeSender()
    SendApproved(s2, FrozenClock(), repos).run(cap=10)
    assert s2.sent == []


def test_state_advances_to_sent(repos, one_approved):
    SendApproved(FakeSender(), FrozenClock(), repos).run(cap=10)
    assert repos.outreach.get(one_approved.id).state == S.SENT


# --- reserve before send (R1) -------------------------------------------------


def _outbound(repos, founder):
    outreach = repos.outreach.get_by_founder_id(founder.id)
    return repos.message.get_outbound(outreach.id, outreach.sequence_step)


def test_reservation_is_committed_before_the_sender_is_called(repos, one_approved):
    """Sending before reserving is THE double-send bug (spec §13). The
    sender observes the session at call time: the row must already carry
    `sent_at` and that write must already be committed."""
    seen = {}

    class ObservingSender(FakeSender):
        def send(self, *a, **kw):
            row = _outbound(repos, one_approved)
            seen["sent_at"] = row.sent_at
            seen["commits"] = repos.session.commit_calls
            return super().send(*a, **kw)

    SendApproved(ObservingSender(), FrozenClock(), repos).run(cap=10)
    assert seen["sent_at"] is not None
    assert seen["commits"] >= 1


def test_reservation_uses_the_clock(repos, one_approved):
    at = datetime(2026, 9, 11, 10, 30, tzinfo=timezone.utc)
    SendApproved(FakeSender(), FrozenClock(at), repos).run(cap=10)
    assert _outbound(repos, one_approved).sent_at == at


def test_record_step_persists_gmail_ids_and_commits(repos, one_approved):
    result = SendApproved(FakeSender(), FrozenClock(), repos).run(cap=10)
    row = _outbound(repos, one_approved)
    assert result.sent == 1
    assert row.gmail_message_id == "msg-1"
    assert row.gmail_thread_id == "thread-1"
    assert repos.session.commit_calls == 2  # reserve commit + record commit


def test_next_action_at_is_sent_at_plus_followup_day_1(repos, one_approved):
    at = datetime(2026, 9, 11, 10, 30, tzinfo=timezone.utc)
    SendApproved(FakeSender(), FrozenClock(at), repos, followup_day_1=5).run(cap=10)
    assert repos.outreach.get(one_approved.id).next_action_at == at + timedelta(days=5)


def test_rowcount_zero_means_no_send(repos, one_approved):
    """The conditional UPDATE reporting 0 rows means someone else reserved
    (or the approval vanished). Whatever the reason: never send."""
    calls = []
    original = repos.message.reserve_for_send

    def losing_reserve(message_id, now):
        calls.append(message_id)
        return 0

    repos.message.reserve_for_send = losing_reserve
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=10)
    repos.message.reserve_for_send = original
    assert calls == [_outbound(repos, one_approved).id]
    assert s.sent == []
    assert result.sent == 0
    assert result.skipped_not_reservable == 1
    assert repos.outreach.get(one_approved.id).state == S.APPROVED


def test_unapproved_row_cannot_be_reserved_even_if_outreach_says_approved(repos, one_approved):
    """Belt and braces with the DB CHECK (spec §13): the reservation
    predicate itself requires `approved_at IS NOT NULL`."""
    _outbound(repos, one_approved).approved_at = None
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=10)
    assert s.sent == []
    assert result.skipped_not_reservable == 1
    assert _outbound(repos, one_approved).sent_at is None


def test_fake_reserve_models_the_conditional_update(repos, one_approved):
    """The fake must behave like `UPDATE ... WHERE sent_at IS NULL AND
    approved_at IS NOT NULL`, or the crash test above proves nothing."""
    row = _outbound(repos, one_approved)
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    assert repos.message.reserve_for_send(row.id, now) == 1
    assert row.sent_at == now
    assert repos.message.reserve_for_send(row.id, now) == 0      # already reserved
    assert repos.message.reserve_for_send(9999, now) == 0        # no such row
    fresh = repos.message.upsert(outreach_id=999, sequence_step=0, direction="outbound",
                                 body="b", approved_at=None)
    assert repos.message.reserve_for_send(fresh.id, now) == 0    # not approved
    assert fresh.sent_at is None


# --- crash between reserve and send: surfaced, never re-sent ------------------


def test_crash_leaves_reservation_and_no_gmail_id(repos, one_approved):
    class ExplodingSender(FakeSender):
        def send(self, *a, **kw):
            super().send(*a, **kw)
            raise RuntimeError("secret provider detail")

    with pytest.raises(RuntimeError):
        SendApproved(ExplodingSender(), FrozenClock(), repos).run(cap=10)

    row = _outbound(repos, one_approved)
    outreach = repos.outreach.get(one_approved.id)
    assert row.sent_at is not None
    assert row.gmail_message_id is None and row.gmail_thread_id is None
    assert outreach.state == S.APPROVED
    assert "needs manual check" in (outreach.last_error or "")
    assert "secret provider detail" not in (outreach.last_error or "")


def test_next_run_flags_needs_manual_check_and_does_not_send(repos, one_approved, caplog):
    class ExplodingSender(FakeSender):
        def send(self, *a, **kw):
            super().send(*a, **kw)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        SendApproved(ExplodingSender(), FrozenClock(), repos).run(cap=10)

    s2 = FakeSender()
    with caplog.at_level(logging.WARNING):
        result = SendApproved(s2, FrozenClock(), repos).run(cap=10)
    assert s2.sent == []
    assert result.sent == 0
    assert result.needs_manual_check == 1
    assert result.skipped_not_reservable == 0
    assert any("manual check" in r.getMessage() for r in caplog.records)
    assert repos.outreach.get(one_approved.id).state == S.APPROVED


def test_sender_failure_stops_the_run_before_the_next_founder(repos, five_approved):
    """Spec §13: Gmail auth/quota errors pause sending rather than burning
    through the queue. The first failure halts the run; nothing after it
    is reserved or sent."""
    class ExplodingSender(FakeSender):
        def send(self, *a, **kw):
            super().send(*a, **kw)
            raise RuntimeError("boom")

    s = ExplodingSender()
    with pytest.raises(RuntimeError):
        SendApproved(s, FrozenClock(), repos).run(cap=10)
    assert len(s.sent) == 1
    reserved = [f for f in five_approved if _outbound(repos, f).sent_at is not None]
    assert len(reserved) == 1


def test_exception_message_is_not_logged(repos, one_approved, caplog):
    class ExplodingSender(FakeSender):
        def send(self, *a, **kw):
            raise RuntimeError("secret provider detail")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError):
            SendApproved(ExplodingSender(), FrozenClock(), repos).run(cap=10)
    assert all("secret provider detail" not in r.getMessage() for r in caplog.records)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


# --- daily cap ---------------------------------------------------------------


def test_cap_counts_messages_already_sent_today(repos, five_approved, sent_today):
    """Re-running the CLI must not exceed `daily_send_cap`: with 2 already
    sent today and cap=3, only 1 more goes out."""
    sent_today(2)
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=3)
    assert len(s.sent) == 1
    assert result.sent == 1
    assert result.skipped_cap == 4


def test_cap_ignores_messages_sent_before_today_utc(repos, five_approved, sent_today):
    clock = FrozenClock(datetime(2026, 9, 11, 0, 30, tzinfo=timezone.utc))
    sent_today(2, at=datetime(2026, 9, 10, 23, 59, tzinfo=timezone.utc))
    s = FakeSender()
    SendApproved(s, clock, repos).run(cap=3)
    assert len(s.sent) == 3


def test_cap_already_reached_sends_nothing_and_reserves_nothing(repos, five_approved, sent_today):
    sent_today(3)
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=3)
    assert s.sent == []
    assert result.skipped_cap == 5
    assert all(_outbound(repos, f).sent_at is None for f in five_approved)


def test_limit_bounds_a_single_run_below_the_cap(repos, five_approved):
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=10, limit=2)
    assert len(s.sent) == 2
    assert result.sent == 2
    assert result.skipped_cap == 3


def test_delay_hook_runs_between_sends_not_after_the_last(repos, five_approved):
    pauses = []
    SendApproved(FakeSender(), FrozenClock(), repos,
                 pause_between_sends=lambda: pauses.append(1)).run(cap=3)
    assert len(pauses) == 2


# --- compose at send time (R2) -----------------------------------------------


def test_wire_text_is_composed_and_the_stored_body_is_untouched(repos, one_approved):
    s = FakeSender()
    row = _outbound(repos, one_approved)
    stored_body = row.body
    SendApproved(s, FrozenClock(), repos, signature="Suryansh").run(cap=10)
    assert s.sent[0]["body"] == f"Hi Madhav,\n\n{stored_body}\n\nSuryansh\n\n{OPT_OUT_LINE}"
    assert s.sent[0]["subject"] == row.subject
    assert _outbound(repos, one_approved).body == stored_body


def test_attachment_path_is_passed_to_the_sender(repos, one_approved):
    class RecordingSender(FakeSender):
        def send(self, to, subject, body, attachment_path):
            self.sent.append({"to": to, "subject": subject, "body": body, "attachment": attachment_path})
            return ("m", "t")

    s = RecordingSender()
    SendApproved(s, FrozenClock(), repos, attachment_path="/tmp/resume.pdf").run(cap=10)
    assert s.sent[0]["attachment"] == "/tmp/resume.pdf"


# --- only APPROVED can be sent ---------------------------------------------------


def test_a_non_approved_outreach_returned_by_the_listing_is_refused(repos, one_approved, monkeypatch):
    """`list_by_state` going stale (or a fake that lies) must hit the state
    machine, not the sender: sending from anything but APPROVED is
    impossible."""
    repos.outreach.get(one_approved.id).state = str(S.PENDING_APPROVAL)
    monkeypatch.setattr(repos.founder, "list_by_state", lambda state: [one_approved])
    s = FakeSender()
    with pytest.raises(IllegalTransition):
        SendApproved(s, FrozenClock(), repos).run(cap=10)
    assert s.sent == []
    assert _outbound(repos, one_approved).sent_at is None


def test_founder_without_resolved_email_is_skipped_not_reserved(repos, one_approved):
    repos.outreach.get(one_approved.id).resolved_email = None
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=10)
    assert s.sent == []
    assert result.errors == 1
    assert _outbound(repos, one_approved).sent_at is None


def test_founder_without_an_outbound_row_is_skipped(repos, one_approved):
    repos.message.rows.clear()
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=10)
    assert s.sent == []
    assert result.errors == 1


# --- send to self (test path) ------------------------------------------------


def test_send_to_self_sends_the_composed_text_without_reserving_or_transitioning(repos, one_approved):
    s = FakeSender()
    n = SendApproved(s, FrozenClock(), repos, signature="Suryansh").send_to_self(
        "me@example.com", limit=1
    )
    assert n == 1
    assert s.sent[0]["to"] == "me@example.com"
    assert s.sent[0]["body"].startswith("Hi Madhav,\n\n")
    row = _outbound(repos, one_approved)
    assert row.sent_at is None and row.gmail_message_id is None
    assert repos.outreach.get(one_approved.id).state == S.APPROVED
    assert repos.session.commit_calls == 0


def test_send_to_self_respects_limit(repos, five_approved):
    s = FakeSender()
    assert SendApproved(s, FrozenClock(), repos).send_to_self("me@example.com", limit=2) == 2
    assert {d["to"] for d in s.sent} == {"me@example.com"}
