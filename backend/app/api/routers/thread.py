"""Task 10: the operator's read view of a whole outreach thread, and the
one place a wrong reply label is hand-corrected (spec §5/§16, T9-1).

Routers hold no business logic (`app/api/main.py`'s module docstring):

- `GET /api/outreach/{id}/thread` is pure DTO translation over
  `OutreachRepo`/`FounderRepo`/`CompanyRepo`/`MessageRepo` — no rule of
  its own, just outbound+inbound rows merged into one time-ordered
  timeline plus a deep link into the operator's own Gmail.
- `POST /api/messages/{id}/label` validates the label against
  `app.domain.replies.LABELS` (422 on anything else — the only "rule"
  this router owns) and hands off everything about what a corrected
  label MEANS to `ClassifyReply.apply_label`
  (`app/application/classify_reply.py`) — the exact method the automatic
  classifier itself calls, extracted there specifically so the automatic
  and manual paths cannot drift. A wrong label must never be silently
  load-bearing: correcting it undoes the label's *consequences*
  (`apply_label`'s T9-1 restore path), not just its text.

Commits exactly once on success, never on a refusal — same pattern as
`app/api/routers/manual.py`: `get_repos` never commits, so a 404/422
leaves nothing on the session to commit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Repos, get_repos
from app.api.schemas import LabelCorrectionIn, MessageOut, OkOut, ThreadOut
from app.application.classify_reply import ClassifyReply, ClassifyRepos
from app.application.review_outreach import OutreachNotFound
from app.domain.replies import LABELS

router = APIRouter(tags=["thread"])


def _get_outreach(repos: Repos, outreach_id: int):
    """Raises the SAME `OutreachNotFound` (Task 4, ruling T4-1) `queue.py`
    and `manual.py` already catch by type rather than string-matching a
    message — this route has no use case of its own to raise it, but the
    404 case is identical, so it reuses the type instead of growing a
    second `ValueError` subclass that means the same thing."""
    outreach = repos.outreach.get(outreach_id)
    if outreach is None:
        raise OutreachNotFound(f"no outreach with id {outreach_id}")
    return outreach


def _gmail_url(messages) -> str | None:
    """A deep link into the operator's own Gmail inbox for this thread —
    `None` only when no message in it carries a Gmail thread id yet (e.g.
    a draft that was never sent)."""
    thread_id = next((m.gmail_thread_id for m in messages if m.gmail_thread_id), None)
    if not thread_id:
        return None
    return f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"


def _sort_key(message):
    """Outbound rows are timestamped by `sent_at`, inbound rows by
    `received_at` — exactly one of the two is ever set on a given row, so
    this single key merges both kinds into one chronological timeline."""
    return message.sent_at or message.received_at


def _to_message_out(m) -> MessageOut:
    return MessageOut(
        id=m.id,
        direction=m.direction,
        sequence_step=m.sequence_step,
        subject=m.subject,
        body=m.body,
        sent_at=m.sent_at,
        received_at=m.received_at,
        label=m.label,
        confidence=m.confidence,
        rationale=m.rationale,
        corrected=bool(getattr(m, "corrected", False)),
    )


def _classify_repos(repos: Repos) -> ClassifyRepos:
    return ClassifyRepos(
        message=repos.message, outreach=repos.outreach, session=repos.session,
        settings=getattr(repos, "settings", None),
    )


@router.get("/outreach/{outreach_id}/thread", response_model=ThreadOut)
def get_thread(outreach_id: int, repos: Repos = Depends(get_repos)) -> ThreadOut:
    try:
        outreach = _get_outreach(repos, outreach_id)
    except OutreachNotFound:
        raise HTTPException(404, "no such outreach")

    founder = repos.founder.get(outreach.founder_id)
    company = repos.company.get(founder.company_id) if founder is not None else None
    messages = sorted(repos.message.list_by_outreach(outreach_id), key=_sort_key)

    return ThreadOut(
        outreach_id=outreach.id,
        company_name=getattr(company, "name", None) if company is not None else None,
        founder_name=getattr(founder, "full_name", None) if founder is not None else None,
        to=outreach.resolved_email,
        state=str(outreach.state),
        gmail_url=_gmail_url(messages),
        messages=[_to_message_out(m) for m in messages],
    )


@router.post("/messages/{message_id}/label", response_model=OkOut)
def correct_label(message_id: int, payload: LabelCorrectionIn, repos: Repos = Depends(get_repos)) -> OkOut:
    if payload.label not in LABELS:
        raise HTTPException(422, f"invalid label: {payload.label!r} - must be one of {LABELS}")

    message = repos.message.get(message_id)
    if message is None:
        raise HTTPException(404, "no such message")

    # `ClassifyReply.apply_label` is the SAME method `_classify_one` uses
    # for the automatic path (Task 10 brief: "extract it ... so the
    # automatic and manual paths cannot drift"). `classifier`/`clock` are
    # never touched by `apply_label` itself - only `_classify_one` (the
    # automatic run loop) uses them - so a bare `ClassifyReply` built with
    # neither is exactly enough machinery for this one call.
    use_case = ClassifyReply(classifier=None, repos=_classify_repos(repos), clock=None)
    use_case.apply_label(message, payload.label, corrected=True)

    repos.session.commit()
    return OkOut()
