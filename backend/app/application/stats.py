"""Pipeline funnel bucketing (Phase 2 design spec §7, `GET /stats`): groups
the outreach state machine's states into the handful of pipeline-stage
buckets a dashboard shows as a funnel, mirroring the CLI's own stage order
(`sync`/`scrape` -> `resolve` -> `draft` -> `review` -> `send`). A rule, so
it lives here rather than in the API router (`app/api/routers/stats.py`) —
the CLI or a future report command can reach it too.
"""

from __future__ import annotations

from typing import Callable

from app.domain.states import OutreachState as S

# Every OutreachState maps to exactly one bucket, in pipeline order.
# `NEEDS_EMAIL` / `NEEDS_MANUAL_EMAIL` land in "new": from a dashboard's
# point of view a founder still short an email address hasn't left the
# top of the funnel yet.
FUNNEL_BUCKETS: dict[str, tuple[S, ...]] = {
    "new": (S.NEW, S.NEEDS_EMAIL, S.NEEDS_MANUAL_EMAIL),
    "ready_to_draft": (S.READY_TO_DRAFT,),
    "pending_approval": (S.PENDING_APPROVAL,),
    "approved": (S.APPROVED,),
    "sent": (S.SENT,),
    "replied": (S.REPLIED,),
    "closed": (S.UNRESOLVED, S.DISCARDED, S.BOUNCED, S.FAILED, S.CLOSED),
}


def funnel_counts(list_by_state: Callable[[S], list]) -> dict[str, int]:
    """`list_by_state` is `founder.list_by_state` (any repo with that
    signature). Returns one count per bucket in `FUNNEL_BUCKETS`, summing
    every raw state that rolls up into it."""
    return {
        bucket: sum(len(list_by_state(state)) for state in states)
        for bucket, states in FUNNEL_BUCKETS.items()
    }
