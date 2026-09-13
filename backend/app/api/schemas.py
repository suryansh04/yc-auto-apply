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
