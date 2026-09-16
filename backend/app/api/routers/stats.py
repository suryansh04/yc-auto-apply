"""GET /api/stats: the funnel counts, verifier quota and recent jobs the
dashboard's overview needs. Translation only (DTO <- repos) — no business
logic, per `app/api/main.py`'s module docstring."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import Repos, get_repos
from app.api.schemas import JobOut, QuotaOut, StatsOut
from app.config import get_operator_settings
from app.domain.states import OutreachState

router = APIRouter(tags=["stats"])

# The free-tier email verifier's daily cap (spec §9: "~2.5 verifications per
# founder" inside a 100/day quota — see `app/application/resolve_emails.py`).
# Not tracked live anywhere yet, so it is a constant here rather than a
# fabricated "used" count.
EMAIL_VERIFIER_DAILY_LIMIT = 100


@router.get("/stats", response_model=StatsOut)
def get_stats(repos: Repos = Depends(get_repos)) -> StatsOut:
    # T3-1: raw OutreachState keys, every state present (even at zero) so the
    # frontend never has to guess whether a missing key means zero or an
    # error. Grouping states for display is the frontend's job, not this
    # endpoint's — folding e.g. needs_manual_email into a coarser bucket
    # would hide exactly the action queue the dashboard exists to surface.
    counts = {state.value: len(repos.founder.list_by_state(state)) for state in OutreachState}

    quota = QuotaOut(
        provider="email-verifier",
        configured=bool(get_operator_settings().email_verifier_api_key),
        daily_limit=EMAIL_VERIFIER_DAILY_LIMIT,
    )

    jobs = [
        JobOut(
            id=j.id,
            kind=j.kind,
            status=j.status,
            created_by=j.created_by,
            started_at=j.started_at,
            finished_at=j.finished_at,
            done=j.done,
            total=j.total,
            summary=j.summary,
            error=j.error,
        )
        for j in repos.job.list_recent()
    ]

    return StatsOut(counts=counts, quota=quota, jobs=jobs)
