"""Task 9 (Phase 2, spec §5): label every unclassified inbound reply and
decide what the label means for the outreach.

The single most important rule under test here: a machine reply
(AUTO_REPLY / OUT_OF_OFFICE) must NEVER be able to end an outreach - only
a human label (INTERESTED / REJECTED / REFERRAL / UNCLEAR) does, and only
by going through `assert_transition`.

Beyond the brief's own seven tests, this file also pins:
- REFERRAL and AUTO_REPLY themselves (the brief only exercises INTERESTED
  as the "cancels" case and OUT_OF_OFFICE as the "doesn't" case - the
  other two labels in each bucket are not automatically covered by that);
- a label arriving with whitespace/lowercase (not just a wholly different
  word) still normalises to UNCLEAR, never crashes, never stores raw;
- confidence is persisted even when very low - the operator still needs
  to see it, not have it silently dropped or clamped;
- one classifier failure among several replies does not stop the run -
  the other replies still get classified, and result.errors counts only
  the failure;
- an already-REPLIED outreach (e.g. a second reply landing after the
  first one already closed the sequence) is not crashed by a second
  attempted SENT -> REPLIED transition - the row is still labelled;
- run(limit=...) processes only that many rows, leaving the rest for a
  later run;
- an empty run (nothing unclassified) returns an all-zero result and
  never calls the classifier.
"""
from datetime import datetime, timezone

import pytest

from app.application.classify_reply import ClassifyReply, ClassifyRepos
from app.domain.states import OutreachState as S


def _repos(repos) -> ClassifyRepos:
    return ClassifyRepos(message=repos.message, outreach=repos.outreach, session=repos.session)


class _FixedClock:
    def now(self):
        return datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _clock() -> _FixedClock:
    return _FixedClock()


# --- Brief's own tests -------------------------------------------------------


def test_an_interested_reply_moves_the_row_to_replied_and_stops_follow_ups(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "INTERESTED", "confidence": 0.9, "rationale": "asks to talk"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    outreach = repos.outreach.get(unlabelled_reply.outreach.id)
    assert outreach.state == S.REPLIED and outreach.next_action_at is None


def test_an_out_of_office_is_recorded_but_changes_nothing(repos, unlabelled_reply, fake_classifier):
    before = repos.outreach.get(unlabelled_reply.outreach.id).next_action_at
    fake_classifier.next = {"label": "OUT_OF_OFFICE", "confidence": 0.95, "rationale": "auto"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    outreach = repos.outreach.get(unlabelled_reply.outreach.id)
    assert outreach.state == S.SENT and outreach.next_action_at == before


def test_the_label_confidence_and_rationale_are_all_persisted(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "REJECTED", "confidence": 0.8, "rationale": "not hiring interns"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    row = repos.message.get(unlabelled_reply.message.id)
    assert (row.label, row.confidence, row.rationale) == ("REJECTED", 0.8, "not hiring interns")
    assert row.classified_at is not None


def test_an_already_labelled_reply_is_not_reclassified(repos, labelled_reply, fake_classifier):
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    assert fake_classifier.calls == []


def test_an_unknown_label_from_the_model_becomes_unclear(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "MAYBE", "confidence": 0.5, "rationale": "?"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    assert repos.message.get(unlabelled_reply.message.id).label == "UNCLEAR"


def test_a_classifier_failure_leaves_the_reply_unlabelled_and_visible(repos, unlabelled_reply, exploding_classifier):
    result = ClassifyReply(exploding_classifier, _repos(repos), _clock()).run()
    assert result.errors == 1
    assert repos.message.get(unlabelled_reply.message.id).label is None


def test_the_classifier_sees_the_thread_not_just_the_reply(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "UNCLEAR", "confidence": 0.4, "rationale": "short"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    ctx = fake_classifier.calls[0]
    assert ctx["our_message"] and ctx["reply_body"]


# --- Added beyond the brief ---------------------------------------------------


def test_a_referral_also_cancels_the_sequence(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "REFERRAL", "confidence": 0.7, "rationale": "points elsewhere"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    outreach = repos.outreach.get(unlabelled_reply.outreach.id)
    assert outreach.state == S.REPLIED and outreach.next_action_at is None


def test_an_auto_reply_is_recorded_but_changes_nothing(repos, unlabelled_reply, fake_classifier):
    before = repos.outreach.get(unlabelled_reply.outreach.id).next_action_at
    fake_classifier.next = {"label": "AUTO_REPLY", "confidence": 0.99, "rationale": "bot ack"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    outreach = repos.outreach.get(unlabelled_reply.outreach.id)
    assert outreach.state == S.SENT and outreach.next_action_at == before
    row = repos.message.get(unlabelled_reply.message.id)
    assert row.label == "AUTO_REPLY"


def test_a_label_with_whitespace_and_lowercase_normalises_to_unclear(repos, unlabelled_reply, fake_classifier):
    # "interested " is NOT the same value as "INTERESTED" - a sloppy or
    # malformed label must never be silently coerced into the real label
    # it merely resembles; it is treated exactly like a wholly unknown
    # string (e.g. "MAYBE") and routed to UNCLEAR instead.
    fake_classifier.next = {"label": " interested ", "confidence": 0.5, "rationale": "sloppy model output"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    row = repos.message.get(unlabelled_reply.message.id)
    assert row.label == "UNCLEAR"


def test_a_low_confidence_label_is_still_persisted_as_given(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "UNCLEAR", "confidence": 0.12, "rationale": "hard to tell"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    row = repos.message.get(unlabelled_reply.message.id)
    assert row.confidence == 0.12


def test_one_failure_among_several_replies_does_not_stop_the_run(repos, two_unlabelled_replies, flaky_classifier):
    result = ClassifyReply(flaky_classifier, _repos(repos), _clock()).run()
    assert result.errors == 1
    assert result.classified == 1
    first, second = two_unlabelled_replies
    labels = {repos.message.get(first.id).label, repos.message.get(second.id).label}
    assert labels == {None, "UNCLEAR"}


def test_a_reply_on_an_outreach_already_past_sent_is_labelled_but_state_is_left_alone(
    repos, unlabelled_reply, fake_classifier,
):
    # A second reply lands on a thread whose outreach already moved to
    # REPLIED (e.g. an earlier reply already closed the sequence). SENT ->
    # REPLIED via a SECOND attempted transition is illegal
    # (`app.domain.states._LEGAL` has no REPLIED -> REPLIED edge) - the row
    # must still be labelled, never crash the run.
    repos.outreach.upsert(founder_id=unlabelled_reply.founder.id, state=str(S.REPLIED))
    fake_classifier.next = {"label": "INTERESTED", "confidence": 0.9, "rationale": "keen"}

    result = ClassifyReply(fake_classifier, _repos(repos), _clock()).run()

    assert result.classified == 1
    row = repos.message.get(unlabelled_reply.message.id)
    assert row.label == "INTERESTED"
    assert repos.outreach.get(unlabelled_reply.outreach.id).state == S.REPLIED


def test_a_human_label_on_an_already_bounced_outreach_never_overwrites_bounced(
    repos, unlabelled_reply, fake_classifier,
):
    # Fix round 1 (review): the previous "already past SENT" test above
    # starts AND ends at REPLIED, so it can't tell a guarded write
    # (assert_transition + IllegalTransition caught) apart from an
    # unconditional one (`outreach.state` written no matter what) - both
    # leave the row at REPLIED either way. BOUNCED is a genuinely
    # DIFFERENT terminal state: SENT -> REPLIED is illegal from BOUNCED
    # too (`app.domain.states._LEGAL` has no BOUNCED -> REPLIED edge), so
    # an unguarded write would visibly stamp REPLIED over a bounce - this
    # is the case that actually distinguishes "guard present" from "guard
    # missing".
    repos.outreach.upsert(founder_id=unlabelled_reply.founder.id, state=str(S.BOUNCED))
    fake_classifier.next = {"label": "INTERESTED", "confidence": 0.9, "rationale": "keen"}

    result = ClassifyReply(fake_classifier, _repos(repos), _clock()).run()

    assert result.classified == 1
    row = repos.message.get(unlabelled_reply.message.id)
    assert row.label == "INTERESTED"
    # The load-bearing assertion: BOUNCED must survive untouched, never
    # silently become REPLIED.
    assert repos.outreach.get(unlabelled_reply.outreach.id).state == S.BOUNCED


def test_limit_processes_only_that_many_rows(repos, two_unlabelled_replies, fake_classifier):
    fake_classifier.next = {"label": "UNCLEAR", "confidence": 0.3, "rationale": "short"}
    result = ClassifyReply(fake_classifier, _repos(repos), _clock()).run(limit=1)
    assert result.classified == 1
    assert len(fake_classifier.calls) == 1


def test_an_empty_run_touches_nothing_and_never_calls_the_classifier(repos, fake_classifier):
    result = ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    assert result.classified == 0 and result.replied == 0 and result.ignored == 0 and result.errors == 0
    assert fake_classifier.calls == []


# --- Task 10 fix round 1 (review): dedicated branch coverage for apply_label --
#
# `apply_label`'s human->human (and machine->machine) case relies on
# `_mark_replied`/`_restore_sent` never being CALLED at all - not on their
# own `assert_transition` guards catching a bad attempt after the fact.
# The end-to-end assertion alone (state/next_action_at unchanged) can't
# tell those two apart: a REPLIED outreach corrected human->human would
# land on the same unchanged state whether `apply_label` skipped the
# transition attempt entirely, or attempted an illegal REPLIED -> REPLIED
# and had `assert_transition` swallow it. This test distinguishes them
# directly by making `_mark_replied`/`_restore_sent` raise if invoked.


def test_correcting_human_to_human_never_even_attempts_a_transition(repos, labelled_reply, fake_classifier):
    use_case = ClassifyReply(fake_classifier, _repos(repos), _clock())

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("human -> human must not attempt any transition")

    use_case._mark_replied = _must_not_be_called
    use_case._restore_sent = _must_not_be_called

    before = repos.outreach.get(labelled_reply.outreach.id).next_action_at
    transitioned = use_case.apply_label(labelled_reply.message, "INTERESTED")

    assert transitioned is False
    assert repos.message.get(labelled_reply.message.id).label == "INTERESTED"
    outreach = repos.outreach.get(labelled_reply.outreach.id)
    assert outreach.state == S.REPLIED and outreach.next_action_at == before


# --- Final fix wave (F6): only an inbound row can carry a label ------------


def test_apply_label_refuses_an_outbound_row_by_type_and_writes_nothing(repos, unlabelled_reply, fake_classifier):
    """An outbound row never carries a label (spec §5); running the
    label's transition logic on the parent outreach from one would be a
    state change with no reply behind it. `apply_label` raises the typed
    `NotAnInboundMessage` before touching either row."""
    from app.application.classify_reply import NotAnInboundMessage

    outbound = repos.message.get_outbound(unlabelled_reply.outreach.founder_id, 0)
    assert outbound.direction == "outbound"
    use_case = ClassifyReply(fake_classifier, _repos(repos), _clock())

    with pytest.raises(NotAnInboundMessage) as exc_info:
        use_case.apply_label(outbound, "INTERESTED", corrected=True)

    assert isinstance(exc_info.value, ValueError)
    assert repos.message.get(outbound.id).label is None
    outreach = repos.outreach.get(unlabelled_reply.outreach.id)
    assert outreach.state == S.SENT and outreach.next_action_at is not None
