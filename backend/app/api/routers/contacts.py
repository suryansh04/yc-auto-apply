"""Task 5: `GET /api/contacts` - every company/founder pair the pipeline
knows about, with optional view filters. There is no dedicated use case
for this: it is a read-only listing over `repos.outreach.all()` joined to
its founder and company, with the query params applied as plain filters -
translation, not a decision, so it stays here rather than growing an
`app/application/` module of its own (unlike the write routes, which all
wrap an existing use case).

D19 (spec, restated in the brief's own test): `hiring` is a VIEW control,
never a gate - a non-hiring company is listed by default, same as every
other row. `state`/`hiring`/`batch`/`email_status` narrow the listing;
omitting all four returns everything."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import Repos, get_repos
from app.api.schemas import ContactOut

router = APIRouter(tags=["contacts"])


@router.get("/contacts", response_model=list[ContactOut])
def list_contacts(
    state: str | None = None,
    hiring: bool | None = None,
    batch: str | None = None,
    email_status: str | None = None,
    repos: Repos = Depends(get_repos),
) -> list[ContactOut]:
    out: list[ContactOut] = []
    for outreach in repos.outreach.all():
        if state is not None and str(outreach.state) != state:
            continue

        founder = repos.founder.get(outreach.founder_id)
        if founder is None:
            continue
        company = repos.company.get(founder.company_id) if founder.company_id is not None else None

        is_hiring = bool(getattr(company, "is_hiring", False)) if company is not None else False
        if hiring is not None and is_hiring != hiring:
            continue

        company_batch = getattr(company, "batch", None) if company is not None else None
        if batch is not None and company_batch != batch:
            continue

        resolved_email = getattr(outreach, "resolved_email", None)
        status = "resolved" if resolved_email else "unresolved"
        if email_status is not None and status != email_status:
            continue

        out.append(ContactOut(
            outreach_id=outreach.id,
            founder_name=founder.full_name,
            company_name=getattr(company, "name", None) if company is not None else None,
            domain=getattr(company, "domain", None) if company is not None else None,
            state=str(outreach.state),
            batch=company_batch,
            team_size=getattr(company, "team_size", None) if company is not None else None,
            is_hiring=is_hiring,
            resolved_email=resolved_email,
            email_status=status,
        ))
    return out
