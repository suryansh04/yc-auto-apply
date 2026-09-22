"""Task 5: the manual-email queue over HTTP - the browser peer of
`app.cli`'s `list-manual` / `set-email`. Both drive the exact same
`ResolveManualEmail` use case (`app/application/resolve_manual_email.py`),
so this router holds no rule of its own: address validation
(`normalise_address`), the state guard (only a `NEEDS_MANUAL_EMAIL` row
can be resolved) and the transition through `assert_transition` all live
in the use case. This module is DTO <- use case <- DTO translation and
exception -> status-code mapping only, per `app/api/main.py`'s module
docstring - same pattern as Task 4's `queue.py`.

`POST /api/outreach/{id}/email` commits once, only after the use case's
action succeeds (`app/api/deps.py`'s `get_repos` deliberately does not
commit) - a refused action (missing outreach, wrong state, a malformed
address) leaves nothing on the session to commit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Repos, get_repos
from app.api.schemas import ManualEmailIn, ManualItemOut, OkOut
from app.application.resolve_manual_email import (
    InvalidAddress,
    ManualEmailRepos,
    ManualItem,
    ResolveManualEmail,
)
from app.application.review_outreach import OutreachNotFound
from app.domain.states import IllegalTransition

router = APIRouter(tags=["manual"])


def _manual_repos(repos: Repos) -> ManualEmailRepos:
    """The `ManualEmailRepos` slice of the request's `Repos` bundle - the
    same fields `app.cli._manual_repos` wires from its own session."""
    return ManualEmailRepos(
        founder=repos.founder, company=repos.company,
        outreach=repos.outreach, email_candidate=repos.email_candidate,
    )


def _use_case(repos: Repos) -> ResolveManualEmail:
    return ResolveManualEmail(_manual_repos(repos))


def _to_out(item: ManualItem) -> ManualItemOut:
    company = item.company
    return ManualItemOut(
        outreach_id=item.outreach.id,
        founder_name=item.founder.full_name,
        company_name=getattr(company, "name", None) if company is not None else None,
        domain=getattr(company, "domain", None) if company is not None else None,
        linkedin_url=getattr(item.founder, "linkedin_url", None),
        candidates=[{"address": c.address, "verdict": getattr(c, "verdict", None)}
                   for c in item.candidates],
        published_hint=item.published_hint,
    )


@router.get("/manual", response_model=list[ManualItemOut])
def list_manual(repos: Repos = Depends(get_repos)) -> list[ManualItemOut]:
    return [_to_out(item) for item in _use_case(repos).pending()]


@router.post("/outreach/{outreach_id}/email", response_model=OkOut)
def resolve_email(outreach_id: int, payload: ManualEmailIn, repos: Repos = Depends(get_repos)) -> OkOut:
    if payload.give_up == (payload.address is not None):
        # Same either-or the CLI's `set-email` enforces (`unresolved ==
        # (address is not None)`): exactly one of the two must be given.
        raise HTTPException(422, "give exactly one of address or give_up")

    use_case = _use_case(repos)
    try:
        if payload.give_up:
            use_case.give_up(outreach_id)
        else:
            use_case.set_email(outreach_id, payload.address)
    except OutreachNotFound:
        # Final fix wave (F3, ruling T4-1 applied here too): the 404 / 422
        # / 409 split is decided by exception TYPE, never by matching the
        # message text - `OutreachNotFound` and `InvalidAddress` are both
        # `ValueError` subclasses, so they must be caught before the
        # catch-all below.
        raise HTTPException(404, "no such outreach")
    except InvalidAddress as exc:
        raise HTTPException(422, str(exc))
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    repos.session.commit()
    return OkOut()
