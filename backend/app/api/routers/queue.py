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

from app.api.deps import Repos, build_insight_writer, build_reviser, build_subject_writer, get_repos
from app.api.schemas import (
    CheckIn,
    CheckOut,
    EditIn,
    HighlightOut,
    InsightIn,
    InsightOut,
    OkOut,
    QueueItemOut,
    SubjectsIn,
    SubjectsOut,
    SuggestOut,
    SuggestionOut,
)
from app.application.review_outreach import OutreachNotFound, ReviewItem, ReviewOutreach, ReviewRepos
from app.application.suggest_fixes import run_reviser
from app.application.write_insight import run_insight_writer
from app.application.write_subjects import run_subject_writer
from app.domain import letter
from app.domain.compose import compose_wire_html, compose_wire_text, first_name
from app.domain.highlight import highlights_for
from app.domain.states import IllegalTransition
from app.config import get_operator_settings, get_settings

router = APIRouter(tags=["queue"])

# What the reviser is told about the company paragraph. Every other span
# it is given carries a complaint ("a sentence exceeds 45 words"); this
# one carries an invitation, because there is nothing wrong with the
# paragraph - it is simply the one part of the letter worth a second
# attempt. Saying "skip it" outright is what stops the model rewriting a
# good line for the sake of answering.
INSIGHT_NOTE = (
    "this is the paragraph about the company, and the only researched part "
    "of the letter - offer a sharper version ONLY if you can name their "
    "choice more precisely than this does, and skip it otherwise"
)


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


def _website(company) -> str | None:
    """The company's own site, as a link that opens. YC's data stores some
    bare ("acme.com"); a scheme is added so the browser does not treat it
    as a path on this app. Only http(s) is ever returned."""
    raw = (getattr(company, "website", None) or "").strip() if company is not None else ""
    if not raw:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def _yc_url(company) -> str | None:
    slug = (getattr(company, "slug", None) or "").strip() if company is not None else ""
    return f"https://www.ycombinator.com/companies/{slug}" if slug else None


def _to_out(item: ReviewItem) -> QueueItemOut:
    # `get_operator_settings()`, not `get_settings()`: this route needs the
    # operator's signature, not a database. Same `.env` as `send`, so the
    # preview matches the wire text exactly.
    settings = get_operator_settings()
    signature = settings.signature
    # Same file `send` attaches (`settings.resume_path`); the preview shows
    # its basename so the reviewer knows the resume goes out with it. The
    # FILE, not the setting (2026-09-23): RESUME_PATH now has a default, so
    # a name on screen would otherwise promise an attachment that does not
    # exist - and `send` refuses in exactly that case.
    attachment_name = (
        os.path.basename(settings.resume_path)
        if settings.resume_path and os.path.isfile(settings.resume_path)
        else None
    )
    message = item.message
    body = getattr(message, "body", None) or ""
    source = item.source
    company_name = getattr(item.company, "name", None) if item.company is not None else None
    # Only offered when there is actually a company paragraph to replace:
    # a letter that does not have the assembled shape would have the
    # generic line put in the wrong place, and `use_generic_line` refuses
    # it. Saying so by omission here is what keeps the button from
    # promising something the backend will decline.
    insight = letter.insight_region(body)
    generic = letter.generic_insight(company_name or "") if company_name and insight else None
    body_html = getattr(message, "body_html", None) or None
    return QueueItemOut(
        outreach_id=item.outreach.id,
        company_name=company_name,
        company_one_liner=getattr(item.company, "one_liner", None) if item.company is not None else None,
        company_website=_website(item.company),
        company_yc_url=_yc_url(item.company),
        founder_name=item.founder.full_name,
        founder_title=getattr(item.founder, "title", None),
        to=getattr(item.outreach, "resolved_email", None),
        subject=getattr(message, "subject", None),
        body=body,
        wire_text=compose_wire_text(first_name(item.founder.full_name), body, signature),
        body_html=body_html,
        wire_html=(compose_wire_html(first_name(item.founder.full_name), body_html, signature)
                   if body_html else None),
        word_count=len(body.split()),
        source_kind=source.kind if source is not None else None,
        source_text=source.text if source is not None else None,
        source_publication=source.publication if source is not None else None,
        last_error=getattr(item.outreach, "last_error", None),
        critic_verdict=getattr(message, "critic_verdict", None),
        critic_note=getattr(message, "critic_overall_note", None),
        sequence_step=int(getattr(item.outreach, "sequence_step", 0) or 0),
        attachment_name=attachment_name,
        generic_line=generic,
        insight_text=insight if generic else None,
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

    # The company paragraph is ALWAYS worth a rewrite, whether or not a
    # check complained about it (operator request 2026-09-23: an AI
    # suggestion and the generic line, side by side, on the one paragraph
    # the letter researches). It is appended LAST, so its index is
    # `len(check highlights)` - exactly the index the review screen gives
    # the same paragraph's mark, which is what pairs the two without
    # either side matching on text.
    insight = letter.insight_region(body)
    if insight:
        highlights = highlights + [{"message": INSIGHT_NOTE, "quote": insight}]

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


@router.post("/outreach/{outreach_id}/insight", response_model=InsightOut)
def write_insight(outreach_id: int, payload: InsightIn | None = None,
                  repos: Repos = Depends(get_repos)) -> InsightOut:
    """A company paragraph written from the company's own YC description.

    Operator request (2026-09-23): the **AI suggestion** tab on that
    paragraph was asking the reviser, which only rewrites what a check
    flagged - so on a paragraph that is fine as English it had nothing to
    offer. This asks the other question: read what the founders wrote
    about their own company (`companies.long_description`, synced from
    the YC dataset) and write the paragraph again.

    Advice, like `/suggest`: writes nothing, refuses nothing. Every way of
    having nothing to offer - no key, no description, an unusable answer -
    answers 200 with a null paragraph and a line saying why, never an
    error that would take the letter off the screen with it.
    """
    try:
        item = _use_case(repos).load(outreach_id)
    except (OutreachNotFound, ValueError):
        raise HTTPException(404, "no such outreach")

    company = item.company
    # The YC description, or failing that their launch post and news
    # (2026-09-24) - see `ReviewOutreach.company_material`.
    description = _use_case(repos).company_material(company) if company is not None else ""
    if not description:
        return InsightOut(unavailable="there is nothing about this company to write from - "
                                      "no YC description, launch post or news")

    # `getattr`, not `repos.insight_writer`: the offline `FakeRepos`
    # carries the attribute only when a test injected one, and a test that
    # did not inject one must take the unavailable path rather than crash.
    writer = getattr(repos, "insight_writer", None)
    if writer is None:
        try:
            writer = build_insight_writer(get_settings())
        except RuntimeError as exc:
            # A setting NAME ("SARVAM_API_KEY is not set"), never a key.
            return InsightOut(unavailable=str(exc))

    # What this company has already been given: the paragraph in the
    # letter now, plus whatever the last press of "Another" produced.
    # Without it, "write me another" and "write me one" are the same
    # request and come back the same (live finding 2026-09-23).
    body = getattr(item.message, "body", "") or ""
    avoid = [a for a in ([letter.insight_region(body)] + list(payload.avoid if payload else [])) if a]
    answer = run_insight_writer(
        writer,
        company_name=getattr(company, "name", None) or "",
        one_liner=getattr(company, "one_liner", None) or "",
        description=description,
        avoid=avoid,
    )
    if answer is None:
        return InsightOut(
            unavailable="nothing specific enough to offer from what they wrote about themselves"
        )
    return InsightOut(paragraph=answer["paragraph"], why=answer["why"])


@router.post("/outreach/{outreach_id}/subjects", response_model=SubjectsOut)
def suggest_subjects(outreach_id: int, payload: SubjectsIn | None = None,
                     repos: Repos = Depends(get_repos)) -> SubjectsOut:
    """Three subject lines written for this company.

    Operator request (2026-09-23): "a agent specifically for writing
    random catchy punchy subject". Advice, like `/insight`: writes
    nothing - the operator picks one and it is saved through `edit` - and
    every way of having nothing to offer answers 200 with a reason.

    The subject the letter has now is always in `avoid`, along with any
    the screen has already shown, so asking again means something.
    """
    try:
        item = _use_case(repos).load(outreach_id)
    except (OutreachNotFound, ValueError):
        raise HTTPException(404, "no such outreach")

    writer = getattr(repos, "subject_writer", None)
    if writer is None:
        try:
            writer = build_subject_writer(get_settings())
        except RuntimeError as exc:
            # A setting NAME ("SARVAM_API_KEY is not set"), never a key.
            return SubjectsOut(unavailable=str(exc))

    company = item.company
    body = getattr(item.message, "body", "") or ""
    current = getattr(item.message, "subject", None)
    subjects = run_subject_writer(
        writer,
        company_name=getattr(company, "name", None) or "",
        one_liner=getattr(company, "one_liner", None) or "",
        description=getattr(company, "long_description", None) or "",
        paragraph=letter.insight_region(body) or "",
        avoid=[a for a in [current, *(payload.avoid if payload else [])] if a],
    )
    if not subjects:
        return SubjectsOut(unavailable="nothing usable this time - try again, or keep the one it has")
    return SubjectsOut(subjects=subjects)


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


@router.post("/outreach/{outreach_id}/generic-line", response_model=CheckOut)
def generic_line(outreach_id: int, repos: Repos = Depends(get_repos)) -> CheckOut:
    """Swap the company paragraph for the written generic one.

    Operator request (2026-09-23): a way out for the companies where no
    good specific line can be found, because the realistic alternative to
    a generic line is a fabricated one. It is an edit like any other -
    saved, validated, never refused - so it answers with the checks'
    notes exactly as `edit` does.
    """
    try:
        notes = _use_case(repos).use_generic_line(outreach_id, now=_UTCClock().now())
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    repos.session.commit()
    return _checked(repos, outreach_id, notes, None)


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
                                      now=_UTCClock().now(), body_html=payload.body_html)
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")
    except (ValueError, IllegalTransition) as exc:
        raise _action_error(exc)
    repos.session.commit()
    # A formatted save derived its plain text server-side, so the notes
    # were produced from the STORED body, not from anything in the request.
    return _checked(repos, outreach_id, notes,
                    None if payload.body_html is not None else payload.body)
