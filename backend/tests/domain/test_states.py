import pytest
from app.domain.states import OutreachState as S, assert_transition, IllegalTransition

def test_happy_path_transitions_are_legal():
    for src, dst in [
        (S.NEW, S.NEEDS_EMAIL),
        (S.NEEDS_EMAIL, S.READY_TO_DRAFT),
        (S.NEEDS_EMAIL, S.NEEDS_MANUAL_EMAIL),
        (S.NEEDS_MANUAL_EMAIL, S.READY_TO_DRAFT),
        (S.NEEDS_MANUAL_EMAIL, S.UNRESOLVED),
        (S.READY_TO_DRAFT, S.PENDING_APPROVAL),
        (S.PENDING_APPROVAL, S.APPROVED),
        (S.PENDING_APPROVAL, S.DISCARDED),
        (S.APPROVED, S.SENT),
    ]:
        assert_transition(src, dst)

def test_approved_to_sent_is_legal():
    assert_transition(S.APPROVED, S.SENT)

@pytest.mark.parametrize("src", [s for s in S if s not in (S.APPROVED, S.REPLIED)])
def test_sending_is_only_legal_from_approved_or_replied(src):
    # The invariant that keeps an unapproved email from ever going out -
    # exhaustive over every OutreachState member except APPROVED (a normal
    # send) and REPLIED (Task 10, ruling T9-1 - see the test right below),
    # so a state added later to the enum is covered automatically.
    with pytest.raises(IllegalTransition):
        assert_transition(src, S.SENT)

def test_replied_to_sent_is_legal_only_for_the_label_correction_path():
    # Task 10 (T9-1): the ONE deliberate exception to "sending is only
    # legal from approved" above. `ClassifyReply.apply_label`'s
    # `_restore_sent` is the only code in this codebase that ever calls
    # this transition - an operator correcting a human label (which had
    # cancelled the sequence) back to a machine one, restoring the founder
    # to the follow-up sequence. This state machine has no caller-scoped
    # permissions, so nothing here can enforce "only reachable from that
    # one path" beyond the fact it is the only caller.
    assert_transition(S.REPLIED, S.SENT)

def test_terminal_states_cannot_transition_out():
    for terminal in [S.DISCARDED, S.UNRESOLVED, S.BOUNCED]:
        with pytest.raises(IllegalTransition):
            assert_transition(terminal, S.READY_TO_DRAFT)

@pytest.mark.parametrize("terminal", [S.UNRESOLVED, S.DISCARDED, S.BOUNCED, S.CLOSED])
def test_terminal_states_cannot_even_fail(terminal):
    # Pins the `src not in TERMINAL` guard in the FAILED special-case.
    with pytest.raises(IllegalTransition):
        assert_transition(terminal, S.FAILED)

def test_any_non_terminal_may_fail():
    assert_transition(S.READY_TO_DRAFT, S.FAILED)
    assert_transition(S.NEEDS_EMAIL, S.FAILED)
