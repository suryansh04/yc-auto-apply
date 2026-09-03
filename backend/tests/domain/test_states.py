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

def test_sending_is_only_legal_from_approved():
    # The invariant that keeps an unapproved email from ever going out.
    for src in [S.NEW, S.READY_TO_DRAFT, S.PENDING_APPROVAL, S.NEEDS_EMAIL]:
        with pytest.raises(IllegalTransition):
            assert_transition(src, S.SENT)

def test_terminal_states_cannot_transition_out():
    for terminal in [S.DISCARDED, S.UNRESOLVED, S.BOUNCED]:
        with pytest.raises(IllegalTransition):
            assert_transition(terminal, S.READY_TO_DRAFT)

def test_any_non_terminal_may_fail():
    assert_transition(S.READY_TO_DRAFT, S.FAILED)
    assert_transition(S.NEEDS_EMAIL, S.FAILED)
