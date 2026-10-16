"""Follow-ups for sent emails with no reply (operator request 2026-10-16).

GET  /api/followups                   - threads waiting on a reply
POST /api/followups/{id}/suggest      - three suggested follow-ups (advice)
POST /api/followups/{id}/approve      - approve his chosen follow-up

Sending is the existing `POST /api/send` with `outreach_id`, which sends
an approved follow-up as a reply in the same Gmail thread. Nothing here
sends.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api import deps
from app.api.deps import Repos, get_repos
from app.application.follow_ups import (
    approve_follow_up,
    list_follow_ups,
    suggest_follow_ups,
)
from app.config import get_operator_settings
from app.domain.states import IllegalTransition

router = APIRouter()


class SentStepOut(BaseModel):
    step: int
    body: str
    sent_at: datetime | None = None


class FollowUpItemOut(BaseModel):
    outreach_id: int
    company_name: str
    founder_name: str
    to: str | None = None
    subject: str
    first_email: str
    last_sent_at: datetime | None = None
    follow_ups_sent: int
    due_at: datetime | None = None
    status: str
    approved_body: str | None = None
    research: dict | None = None
    sent: list[SentStepOut] = []


class SuggestIn(BaseModel):
    avoid: list[str] = []


class FollowUpOptionOut(BaseModel):
    style: str = ""
    body: str


class SuggestOut(BaseModel):
    options: list[FollowUpOptionOut] = []
    why: str = ""
    unavailable: str | None = None


class ApproveIn(BaseModel):
    body: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item(repos: Repos, outreach_id: int):
    for item in list_follow_ups(repos, _now()):
        if item.outreach_id == outreach_id:
            return item
    raise HTTPException(404, "no follow-up for that email")


@router.get("/followups", response_model=list[FollowUpItemOut])
def followups(repos: Repos = Depends(get_repos)) -> list[FollowUpItemOut]:
    return [FollowUpItemOut(**item.__dict__) for item in list_follow_ups(repos, _now())]


@router.post("/followups/{outreach_id}/suggest", response_model=SuggestOut)
def suggest(outreach_id: int, payload: SuggestIn | None = None,
            repos: Repos = Depends(get_repos)) -> SuggestOut:
    """Advice: writes nothing, and every way of having nothing to offer
    answers 200 with a reason."""
    item = _item(repos, outreach_id)
    if item.status in ("approved", "done"):
        return SuggestOut(unavailable="this thread has no follow-up left to write")
    writer = getattr(repos, "followup_writer", None)
    if writer is None:
        try:
            writer = deps.build_followup_writer(get_operator_settings())
        except RuntimeError as exc:
            # A setting NAME ("GEMINI_API_KEY is not set"), never a key.
            return SuggestOut(unavailable=str(exc))
    outreach = repos.outreach.get(outreach_id)
    founder = repos.founder.get(outreach.founder_id) if outreach else None
    company = repos.company.get(founder.company_id) if founder else None
    answer = suggest_follow_ups(
        writer, item, description=getattr(company, "long_description", None) or "",
        avoid=payload.avoid if payload else [],
    )
    if answer is None:
        return SuggestOut(unavailable="nothing usable this time - try again, or write your own")
    return SuggestOut(options=[FollowUpOptionOut(**o) for o in answer["options"]], why=answer["why"])


@router.post("/followups/{outreach_id}/approve", response_model=FollowUpItemOut)
def approve(outreach_id: int, payload: ApproveIn,
            repos: Repos = Depends(get_repos)) -> FollowUpItemOut:
    try:
        approve_follow_up(repos, outreach_id, payload.body, _now())
    except LookupError:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise HTTPException(409, str(exc))
    repos.session.commit()
    return FollowUpItemOut(**_item(repos, outreach_id).__dict__)
