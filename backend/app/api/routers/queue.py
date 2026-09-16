"""Task 4: the review queue over HTTP - the browser peer of `app.cli`'s
`review` command. Both drive the exact same `ReviewOutreach` use case
(`app/application/review_outreach.py`), so the CLI reviewer and the
browser reviewer can never disagree about what is approvable: the router
below holds no rule of its own, only DTO <- use case <- DTO translation
and exception -> status-code mapping, per `app/api/main.py`'s module
docstring.

`GET /api/queue` is `ReviewOutreach.pending()`, already in `rank_key`
order (spec §8, D24) - preserved here by iterating the use case's own
list rather than re-sorting. Each write route
(`approve`/`edit`/`reject`/`redraft`) commits the session itself, once,
only after the use case's action succeeds (`app/api/deps.py`'s
`get_repos` deliberately does not commit) - a refused action leaves
nothing on the session to commit.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import Repos, get_repos
from app.api.schemas import EditIn, OkOut, QueueItemOut
from app.application.review_outreach import OutreachNotFound, ReviewItem, ReviewOutreach, ReviewRepos
from app.domain.compose import compose_wire_text, first_name
from app.domain.states import IllegalTransition
from app.config import get_operator_settings

router = APIRouter(tags=["queue"])


class _UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _review_repos(repos: Repos) -> ReviewRepos:
    """The `ReviewRepos` slice of the request's `Repos` bundle - the same
    fields `app.cli._review_repos` wires from its own session."""
    return ReviewRepos(
        founder=repos.founder,
        company=repos.company,
        outreach=repos.outreach,
        message=repos.message,
        news=repos.news,
        launch=repos.launch,
        job_posting=repos.job_posting,
        resume_profile=repos.resume_profile,
    )


def _use_case(repos: Repos) -> ReviewOutreach:
    return ReviewOutreach(_review_repos(repos))


def _action_error(exc: ValueError | IllegalTransition) -> HTTPException:
    # T4-1 (fix round 1): `OutreachNotFound` (a `ValueError` subclass,
    # raised only by `ReviewOutreach._outreach()` for a missing row) is
    # caught ahead of the generic `ValueError` in every handler below, so
    # by the time this runs `exc` is always a refusal of an otherwise-real
    # row (flagged draft, wrong state) - always 409. No string-matching:
    # the not-found/refused distinction lives in the use case's exception
    # type, not in the router.
    return HTTPException(409, str(exc))


def _to_out(item: ReviewItem) -> QueueItemOut:
    # `get_operator_settings()`, not `get_settings()`: this route needs the
    # operator's signature, not a database. Same `.env` as `send`, so the
    # preview matches the wire text exactly.
    settings = get_operator_settings()
    signature = settings.signature
    # Same `.env` value `send` attaches (`settings.resume_path`); the preview
    # shows its basename so the reviewer knows the resume goes out with it.
    attachment_name = os.path.basename(settings.resume_path) if settings.resume_path else None
    message = item.message
    body = getattr(message, "body", None) or ""
    source = item.source
    return QueueItemOut(
        outreach_id=item.outreach.id,
        company_name=getattr(item.company, "name", None) if item.company is not None else None,
        company_one_liner=getattr(item.company, "one_liner", None) if item.company is not None else None,
        founder_name=item.founder.full_name,
        founder_title=getattr(item.founder, "title", None),
        to=getattr(item.outreach, "resolved_email", None),
        subject=getattr(message, "subject", None),
        body=body,
        wire_text=compose_wire_text(first_name(item.founder.full_name), body, signature),
        word_count=len(body.split()),
        source_kind=source.kind if source is not None else None,
        source_text=source.text if source is not None else None,
        source_publication=source.publication if source is not None else None,
        last_error=getattr(item.outreach, "last_error", None),
        critic_verdict=getattr(message, "critic_verdict", None),
        critic_note=getattr(message, "critic_overall_note", None),
        sequence_step=int(getattr(item.outreach, "sequence_step", 0) or 0),
        attachment_name=attachment_name,
    )


@router.get("/queue", response_model=list[QueueItemOut])
def list_queue(repos: Repos = Depends(get_repos)) -> list[QueueItemOut]:
    return [_to_out(item) for item in _use_case(repos).pending()]


@router.get("/queue/{outreach_id}", response_model=QueueItemOut)
def get_queue_item(outreach_id: int, repos: Repos = Depends(get_repos)) -> QueueItemOut:
    try:
        item = _use_case(repos).load(outreach_id)
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except ValueError:
        # `load()`'s other failure (a resolvable outreach with no founder
        # row) is also "there is nothing here to show" for a single-item
        # GET, so it is 404 too - unlike the write routes below, there is
        # no refused-but-real-row case on a plain read.
        raise HTTPException(404, "no such outreach")
    return _to_out(item)


@router.post("/outreach/{outreach_id}/approve", response_model=OkOut)
def approve(outreach_id: int, repos: Repos = Depends(get_repos)) -> OkOut:
    try:
        _use_case(repos).approve(outreach_id, _UTCClock().now())
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    repos.session.commit()
    return OkOut()


@router.post("/outreach/{outreach_id}/reject", response_model=OkOut)
def reject(outreach_id: int, repos: Repos = Depends(get_repos)) -> OkOut:
    try:
        _use_case(repos).reject(outreach_id)
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    repos.session.commit()
    return OkOut()


@router.post("/outreach/{outreach_id}/redraft", response_model=OkOut)
def redraft(outreach_id: int, repos: Repos = Depends(get_repos)) -> OkOut:
    try:
        _use_case(repos).redraft(outreach_id)
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    repos.session.commit()
    return OkOut()


@router.post("/outreach/{outreach_id}/edit")
def edit(outreach_id: int, payload: EditIn, repos: Repos = Depends(get_repos)):
    try:
        errors = _use_case(repos).edit(outreach_id, payload.subject, payload.body)
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    if errors:
        # 422 with the validator's own messages, verbatim (global
        # constraint) - the browser reviewer must see exactly what the CLI
        # reviewer sees in `_review_loop`'s "edit rejected by the
        # validator" branch. Nothing was persisted; no commit.
        return JSONResponse(status_code=422, content={"errors": errors})
    repos.session.commit()
    return {"ok": True}
