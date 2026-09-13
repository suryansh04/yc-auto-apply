"""Response DTOs for the API adapter (Task 3). Routers translate repo/domain
rows into these; nothing here decides anything — that stays in
`app/application/`."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class QuotaOut(BaseModel):
    provider: str
    configured: bool
    daily_limit: int


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    created_by: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    done: int
    total: int | None = None
    summary: str | None = None
    error: str | None = None


class StatsOut(BaseModel):
    counts: dict[str, int]
    quota: QuotaOut
    jobs: list[JobOut]


class QueueItemOut(BaseModel):
    """Task 4: one `PENDING_APPROVAL` row for the browser review screen -
    the same `ReviewItem` the CLI renders, translated to JSON. `wire_text`
    is the composed preview (`compose_wire_text`), never the bare `body`
    (ruling R2) - the browser reviewer must see exactly what will be sent."""

    outreach_id: int
    company_name: str | None = None
    company_one_liner: str | None = None
    founder_name: str
    founder_title: str | None = None
    to: str | None = None
    subject: str | None = None
    body: str
    wire_text: str
    word_count: int
    source_kind: str | None = None
    source_text: str | None = None
    source_publication: str | None = None
    last_error: str | None = None
    critic_verdict: str | None = None
    critic_note: str | None = None
    sequence_step: int


class EditIn(BaseModel):
    subject: str
    body: str


class OkOut(BaseModel):
    ok: bool = True


class ManualCandidateOut(BaseModel):
    """Task 5: one already-judged `email_candidate` row, as `list-manual`
    prints it — address plus the verdict the verifier gave it, in priority
    order (the published-email hint is excluded here; it is
    `ManualItemOut.published_hint`)."""

    address: str
    verdict: str | None = None


class ManualItemOut(BaseModel):
    """Task 5: one `NEEDS_MANUAL_EMAIL` founder for the browser's manual
    queue - the same `ManualItem` the CLI's `list-manual` renders."""

    outreach_id: int
    founder_name: str
    company_name: str | None = None
    domain: str | None = None
    linkedin_url: str | None = None
    candidates: list[ManualCandidateOut]
    published_hint: str | None = None


class ManualEmailIn(BaseModel):
    """Body of `POST /api/outreach/{id}/email`: exactly one of `address`
    (store it and move to READY_TO_DRAFT) or `give_up` (move to
    UNRESOLVED) - the same choice `set-email ADDRESS` / `set-email
    --unresolved` gives the CLI."""

    address: str | None = None
    give_up: bool = False


class SendIn(BaseModel):
    """Body of `POST /api/send`: `limit` caps this one run on top of the
    rolling 24h `daily_send_cap`, same as the CLI's `--limit`."""

    limit: int | None = None


class SendResultOut(BaseModel):
    """Task 5: `SendApproved.run`'s `SendResult`, translated verbatim -
    field names match the dataclass so nothing is renamed in translation."""

    sent: int
    skipped_cap: int
    skipped_not_reservable: int
    needs_manual_check: int
    errors: int


class ContactOut(BaseModel):
    """Task 5: one company/founder pair for the browser's contacts list -
    every outreach row, unfiltered by default (D19: `hiring` is a view
    control, never a gate on who is listed)."""

    outreach_id: int
    founder_name: str
    company_name: str | None = None
    domain: str | None = None
    state: str
    batch: str | None = None
    team_size: int | None = None
    is_hiring: bool = False
    resolved_email: str | None = None
    email_status: str
