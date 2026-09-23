"""`GET /api/applications` - every application, by what happened to it.

Operator request (2026-09-22): "a tab where I can see all applications
and filter via sent / rejected / skipped".

The Founders screen already lists every row and filters by raw state.
This is a different question. State is where a row sits in the machine;
OUTCOME is what became of the application, and the two do not line up -
`sent`, `replied`, `closed` and `bounced` are four states and one
outcome ("it went out"), while `pending_approval` is one state and two
outcomes depending on whether the operator has passed over it.

So this route buckets rather than filters, and the buckets are the words
the operator used. `outcome=` narrows to one; omitting it returns all.

Read-only translation over the repos, like `contacts.py` - no decision is
made here, so there is no use case to wrap.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import Repos, get_repos
from app.api.schemas import ApplicationOut
from app.domain.states import OutreachState as S

router = APIRouter(tags=["applications"])

# One outcome per row, in the order they are decided. A row matches the
# first bucket it qualifies for.
SENT = "sent"
REJECTED = "rejected"
SKIPPED = "skipped"
WAITING = "waiting"
NOT_STARTED = "not_started"

OUTCOMES = [SENT, REJECTED, SKIPPED, WAITING, NOT_STARTED]

# "It left the building" - four states, one fact. A bounced letter was
# still sent; what happened afterwards is the thread's story, not this
# page's.
_SENT_STATES = {S.SENT, S.REPLIED, S.CLOSED, S.BOUNCED}
# Waiting on the operator: drafted and not yet decided, or decided and
# not yet sent.
_WAITING_STATES = {S.PENDING_APPROVAL, S.APPROVED}


def outcome_of(outreach) -> str:
    """Which bucket an outreach row belongs in.

    Skip is checked before waiting and only for a row that is still
    waiting: a skipped letter the operator later approved is "waiting to
    send", not "skipped". (`skipped_at` is cleared on every real decision
    anyway - this is the belt to that braces.)
    """
    state = S(str(outreach.state))
    if state in _SENT_STATES:
        return SENT
    if state is S.DISCARDED:
        return REJECTED
    if state in _WAITING_STATES:
        if state is S.PENDING_APPROVAL and getattr(outreach, "skipped_at", None) is not None:
            return SKIPPED
        return WAITING
    return NOT_STARTED


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    outcome: str | None = None,
    repos: Repos = Depends(get_repos),
) -> list[ApplicationOut]:
    out: list[ApplicationOut] = []
    for outreach in repos.outreach.all():
        row_outcome = outcome_of(outreach)
        if outcome is not None and row_outcome != outcome:
            continue

        founder = repos.founder.get(outreach.founder_id)
        if founder is None:
            continue
        company = (
            repos.company.get(founder.company_id) if founder.company_id is not None else None
        )

        step = int(getattr(outreach, "sequence_step", 0) or 0)
        message = repos.message.get_outbound(outreach.id, step)

        out.append(
            ApplicationOut(
                outreach_id=outreach.id,
                company_name=getattr(company, "name", None),
                founder_name=founder.full_name,
                to=getattr(outreach, "resolved_email", None),
                state=str(outreach.state),
                outcome=row_outcome,
                subject=getattr(message, "subject", None),
                sent_at=_iso(getattr(message, "sent_at", None)),
                skipped_at=_iso(getattr(outreach, "skipped_at", None)),
                last_error=getattr(outreach, "last_error", None),
            )
        )

    # Newest activity first within a bucket: a letter that went out
    # yesterday is more interesting than one that went out last month,
    # and a row with no activity at all sorts last by founder name.
    out.sort(key=lambda a: (a.sent_at or a.skipped_at or "", a.founder_name or ""), reverse=True)
    return out


def _iso(value) -> str | None:
    """A timestamp as ISO text. Tolerates a value that is already text,
    because not every repo behind this route stores a datetime."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
