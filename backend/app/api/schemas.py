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
