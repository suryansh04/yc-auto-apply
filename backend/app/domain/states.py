from enum import StrEnum


class IllegalTransition(Exception):
    pass


class OutreachState(StrEnum):
    NEW = "new"
    NEEDS_EMAIL = "needs_email"
    NEEDS_MANUAL_EMAIL = "needs_manual_email"
    UNRESOLVED = "unresolved"
    READY_TO_DRAFT = "ready_to_draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    REPLIED = "replied"          # Phase 2
    CLOSED = "closed"            # Phase 2
    DISCARDED = "discarded"
    BOUNCED = "bounced"
    FAILED = "failed"


S = OutreachState
TERMINAL = {S.UNRESOLVED, S.DISCARDED, S.BOUNCED, S.CLOSED}

_LEGAL: dict[OutreachState, set[OutreachState]] = {
    S.NEW: {S.NEEDS_EMAIL},
    S.NEEDS_EMAIL: {S.READY_TO_DRAFT, S.NEEDS_MANUAL_EMAIL},
    S.NEEDS_MANUAL_EMAIL: {S.READY_TO_DRAFT, S.UNRESOLVED},
    S.READY_TO_DRAFT: {S.PENDING_APPROVAL},
    S.PENDING_APPROVAL: {S.APPROVED, S.DISCARDED, S.READY_TO_DRAFT},
    # PENDING_APPROVAL is the "send it back to review" edge (operator
    # request, 2026-09-22): an approved letter he has changed his mind
    # about returns to the queue instead of being discarded or sent. The
    # caller must clear `approved_at` with it - a PENDING_APPROVAL row
    # carrying an approval is the inconsistency `send`'s own guard warns
    # about.
    S.APPROVED: {S.SENT, S.PENDING_APPROVAL},
    S.SENT: {S.REPLIED, S.READY_TO_DRAFT, S.CLOSED, S.BOUNCED},
    # Task 10 (T9-1): REPLIED -> SENT is legal ONLY via the label-correction
    # path (`ClassifyReply.apply_label`'s `_restore_sent`, reached from
    # `POST /api/messages/{id}/label`) - an operator correcting a human
    # label (which had cancelled the sequence) back to a machine one. This
    # state machine has no caller-scoped permissions, so the restriction is
    # enforced by which code actually calls `assert_transition(REPLIED,
    # SENT)`, not by this edge itself; `_classify_one`'s automatic path
    # never attempts it.
    S.REPLIED: {S.CLOSED, S.SENT},
}


def assert_transition(src: OutreachState, dst: OutreachState) -> None:
    if dst is S.FAILED and src not in TERMINAL:
        return
    if dst not in _LEGAL.get(src, set()):
        raise IllegalTransition(f"{src} -> {dst} is not a legal transition")
