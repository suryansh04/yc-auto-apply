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

from app.api.deps import Repos, build_reviser, get_repos
from app.api.schemas import (
    CheckIn,
    CheckOut,
    EditIn,
    HighlightOut,
    OkOut,
    QueueItemOut,
    SuggestOut,
    SuggestionOut,
)
from app.application.review_outreach import OutreachNotFound, ReviewItem, ReviewOutreach, ReviewRepos
from app.application.suggest_fixes import run_reviser
from app.domain.compose import compose_wire_text, first_name
from app.domain.highlight import highlights_for
from app.domain.states import IllegalTransition
from app.config import get_operator_settings, get_settings

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


@router.get("/approved", response_model=list[QueueItemOut])
def list_approved(repos: Repos = Depends(get_repos)) -> list[QueueItemOut]:
    """The letters approved and not yet sent, as whole letters.

    Same shape as `/queue` because it is the same thing at a later
    moment - the operator reads the composed wire text, not a row.
    """
    return [_to_out(item) for item in _use_case(repos).approved()]


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


def _checked(repos: Repos, outreach_id: int, notes: list[str], body: str | None) -> CheckOut:
    """Pair each note with the words it is about, against the body the
    notes were produced from - the text just supplied, or the stored one."""
    text = body
    if text is None:
        item = _use_case(repos).load(outreach_id)
        text = getattr(item.message, "body", "") or ""
    return CheckOut(
        notes=notes,
        highlights=[HighlightOut(**h) for h in highlights_for(notes, text)],
    )


@router.post("/outreach/{outreach_id}/check", response_model=CheckOut)
def check(outreach_id: int, payload: CheckIn | None = None,
          repos: Repos = Depends(get_repos)) -> CheckOut:
    """Re-run the checks and say what they found. Writes nothing.

    Operator decision (2026-09-22): these are suggestions, so asking for
    them is a read, never a refusal - a draft the checks dislike still
    returns 200 with the notes in the body.
    """
    try:
        notes = _use_case(repos).check(
            outreach_id,
            subject=payload.subject if payload else None,
            body=payload.body if payload else None,
        )
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except ValueError as exc:
        raise _action_error(exc)
    return _checked(repos, outreach_id, notes, payload.body if payload else None)


@router.post("/outreach/{outreach_id}/suggest", response_model=SuggestOut)
def suggest(outreach_id: int, payload: CheckIn | None = None,
            repos: Repos = Depends(get_repos)) -> SuggestOut:
    """What to write instead, for each span `/check` highlighted.

    Operator request (2026-09-22): "when i hover over it, it shows
    suggestion just like grammarly use sarvam for suggestion". `/check` is
    deterministic and instant; this is the LLM call, deliberately a second
    request so the highlights appear immediately and the rewrites fill in
    behind them.

    Advice on top of advice, so it cannot fail loudly: an unconfigured or
    failing provider answers 200 with no suggestions (and, for a missing
    key, the NAME of the setting), never an error that would take the
    checks off the screen with it. Writes nothing - there is no commit
    here, and the operator's text is only ever changed by `edit`.
    """
    try:
        item = _use_case(repos).load(outreach_id)
    except (OutreachNotFound, ValueError):
        raise HTTPException(404, "no such outreach")

    body = (payload.body if payload and payload.body is not None else None)
    if body is None:
        body = getattr(item.message, "body", "") or ""
    notes = _use_case(repos).check(
        outreach_id,
        subject=payload.subject if payload else None,
        body=payload.body if payload else None,
    )
    highlights = highlights_for(notes, body)

    # `getattr`, not `repos.reviser`: the offline `FakeRepos` carries the
    # attribute only when a test injected one, and a test that did not
    # inject one must take the unavailable path rather than crash.
    reviser = getattr(repos, "reviser", None)
    if reviser is None:
        try:
            reviser = build_reviser(get_settings())
        except RuntimeError as exc:
            # The message is a setting NAME ("SARVAM_API_KEY is not set"),
            # never a key or a response body - `build_reviser` raises
            # nothing else.
            return SuggestOut(suggestions=[], unavailable=str(exc))

    company_name = getattr(item.company, "name", None) or ""
    return SuggestOut(suggestions=[
        SuggestionOut(
            index=s.index,
            quote=highlights[s.index]["quote"],
            message=highlights[s.index]["message"],
            replacement=s.replacement,
            why=s.why,
        )
        for s in run_reviser(reviser, body, highlights, company_name)
    ])


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


@router.post("/outreach/{outreach_id}/skip", response_model=OkOut)
def skip(outreach_id: int, repos: Repos = Depends(get_repos)) -> OkOut:
    """Record that this draft was passed over.

    Skipping is still not a decision - the state does not change. What
    changes is that a letter the operator has read and moved on from is
    now distinguishable from one he has never reached, which is what the
    Applications screen's "skipped" filter is.
    """
    try:
        _use_case(repos).skip(outreach_id, _UTCClock().now())
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    repos.session.commit()
    return OkOut()


@router.post("/outreach/{outreach_id}/unapprove", response_model=OkOut)
def unapprove(outreach_id: int, repos: Repos = Depends(get_repos)) -> OkOut:
    """Send an approved letter back to the review queue."""
    try:
        _use_case(repos).unapprove(outreach_id)
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


@router.post("/outreach/{outreach_id}/edit", response_model=CheckOut)
def edit(outreach_id: int, payload: EditIn, repos: Repos = Depends(get_repos)) -> CheckOut:
    """Save the operator's text, and say what the checks made of it.

    Operator decision (2026-09-22): this used to answer 422 and persist
    NOTHING when the checks complained, which made them a gate on his own
    typing - the same mistake `approve` was making. The save now always
    happens (a 4xx is reserved for "there is nothing to save" and "no such
    row"), and the notes come back in the body as advice.
    """
    try:
        # The clock matters only for an APPROVED letter, whose approval
        # is re-stamped onto the text the operator just wrote.
        notes = _use_case(repos).edit(outreach_id, payload.subject, payload.body,
                                      now=_UTCClock().now())
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    repos.session.commit()
    return _checked(repos, outreach_id, notes, payload.body)
