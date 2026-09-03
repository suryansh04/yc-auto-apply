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
    S.APPROVED: {S.SENT},
    S.SENT: {S.REPLIED, S.READY_TO_DRAFT, S.CLOSED, S.BOUNCED},
    S.REPLIED: {S.CLOSED},
}


def assert_transition(src: OutreachState, dst: OutreachState) -> None:
    if dst is S.FAILED and src not in TERMINAL:
        return
    if dst not in _LEGAL.get(src, set()):
        raise IllegalTransition(f"{src} -> {dst} is not a legal transition")
