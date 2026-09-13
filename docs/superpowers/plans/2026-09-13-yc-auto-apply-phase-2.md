# YC Auto Apply Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the whole outreach from a browser — start every stage, review and approve drafts with the keyboard, send, and see each founder's reply with its classification and outcome.

**Architecture:** `app/api/` becomes a third adapter beside `cli.py`: FastAPI routers translate DTO → existing use case → DTO, holding no business logic. Long stages run as background-thread jobs tracked in a `jobs` table. Reply polling, classification and follow-up scheduling are new use cases in `application/`. A Vite + React frontend consumes the API.

**Tech Stack:** FastAPI, uvicorn, SQLAlchemy 2.0, Alembic, httpx, respx, pytest; Vite, React 18 (JSX), TanStack Query v5, Vitest, React Testing Library, MSW.

**Spec:** `docs/superpowers/specs/2026-09-13-yc-auto-apply-phase-2-design.md` (amends `docs/superpowers/specs/2026-09-03-yc-auto-apply-design.md`)

## Global Constraints

- **Layering:** `app/domain/` and `app/application/` never import `app/adapters/` or `app/api/`. Enforced by grep in review.
- **Definition of done:** `cd backend && python -m pytest` passes with **no env vars and no network**, and `cd frontend && npm test` passes. Integration tests stay behind `@pytest.mark.integration` (deselected by `addopts`).
- **No business logic in routers.** A rule that belongs in a use case must be reachable from the CLI too.
- **Secrets never leak:** no API key, OAuth token or client secret in any log line, exception message, HTTP response or job row. Errors record the exception **type name**, never `str(exc)`.
- **Transitions** go through `assert_transition` in `app/domain/states.py`. Never write `outreach.state` directly.
- **Commits per unit of work**, matching Phase 1 ruling FR-3: a killed job must not lose work already paid for.
- **LLM provider** comes from `Settings.llm_provider` (`gemini` | `sarvam`) — never hardcode a provider or model id.
- **API binds `127.0.0.1` only.** No auth (spec A3).
- **Gmail scope** for reading is `https://www.googleapis.com/auth/gmail.readonly` (spec A2).
- **Commit trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/app/adapters/db/models.py` | + `Job`; + critic/classification columns on `Message`; + `last_history_id` on `GmailCredential` |
| `backend/alembic/versions/0003_phase2.py` | Migration for all of the above |
| `backend/app/adapters/db/repositories.py` | + `JobRepo`; + `MessageRepo.get_by_gmail_id`, `list_inbound`, `list_by_outreach` |
| `backend/app/application/run_job.py` | `RunJob` — record, run, progress, finish |
| `backend/app/application/poll_replies.py` | `PollReplies` — Gmail history → inbound rows, bounces |
| `backend/app/application/classify_reply.py` | `ClassifyReply` — label one inbound message |
| `backend/app/application/schedule_follow_ups.py` | `ScheduleFollowUps` — due rows to next step or `CLOSED` |
| `backend/app/adapters/gmail_reader.py` | `GmailReader` — history API + `newer_than` fallback |
| `backend/app/adapters/gemini.py`, `sarvam.py` | + classifier and critic prompts/schemas |
| `backend/app/api/main.py` | App factory, CORS, startup recovery |
| `backend/app/api/deps.py` | Session and repo-bundle dependencies |
| `backend/app/api/schemas.py` | Pydantic DTOs |
| `backend/app/api/routers/{stats,queue,manual,send,contacts,thread,jobs}.py` | One router per screen concern |
| `backend/app/api/jobs.py` | Job kinds registry + background runner |
| `frontend/src/api/client.js` | fetch wrapper + TanStack Query hooks |
| `frontend/src/screens/{Pipeline,ReviewQueue,Contacts,Thread}.jsx` | The four screens |
| `frontend/src/components/*.jsx` | Shared table, banner, progress, keyboard hook |

---

## Task 1: Schema for jobs, critic and classification

**Files:**
- Modify: `backend/app/adapters/db/models.py`
- Create: `backend/alembic/versions/0003_phase2.py`
- Test: `backend/tests/adapters/db/test_models_phase2.py`

**Interfaces:**
- Produces: `Job` model (`app.adapters.db.models.Job`) with `id, kind, status, created_by, started_at, finished_at, done, total, summary, error`; `Message.critic_verdict/critic_concerns/critic_overall_note/label/confidence/rationale/classified_at`; `GmailCredential.last_history_id`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/adapters/db/test_models_phase2.py
"""Phase 2 schema: jobs, critic columns, classification columns."""
import pytest
from sqlalchemy import inspect

from app.adapters.db.models import Base, GmailCredential, Job, Message


def test_job_table_columns():
    cols = {c.name for c in Job.__table__.columns}
    assert cols == {
        "id", "kind", "status", "created_by", "started_at", "finished_at",
        "done", "total", "summary", "error",
    }


def test_only_one_active_job_per_kind_is_possible():
    # A partial unique index, not application logic: a double-clicked button
    # must lose to the database.
    idx = {i.name: i for i in Job.__table__.indexes}
    active = idx["uq_jobs_kind_active"]
    assert active.unique is True
    assert [c.name for c in active.columns] == ["kind"]
    assert "queued" in str(active.dialect_options["postgresql"]["where"])


def test_job_status_is_constrained():
    checks = [c for c in Job.__table__.constraints if hasattr(c, "sqltext")]
    text = " ".join(str(c.sqltext) for c in checks)
    for status in ("queued", "running", "succeeded", "failed"):
        assert status in text


def test_message_gains_critic_and_classification_columns():
    cols = {c.name for c in Message.__table__.columns}
    assert {"critic_verdict", "critic_concerns", "critic_overall_note"} <= cols
    assert {"label", "confidence", "rationale", "classified_at"} <= cols


def test_gmail_message_id_is_unique_so_repolling_cannot_duplicate():
    uniques = [i for i in Message.__table__.indexes if i.unique]
    assert any([c.name for c in i.columns] == ["gmail_message_id"] for i in uniques)


def test_credential_stores_the_history_cursor():
    assert "last_history_id" in {c.name for c in GmailCredential.__table__.columns}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/adapters/db/test_models_phase2.py -v`
Expected: FAIL — `ImportError: cannot import name 'Job'`.

- [ ] **Step 3: Add the model and columns**

In `models.py`, after `GmailCredential`:

```python
class Job(Base):
    """A pipeline stage running in the background (spec §4). The partial
    unique index — not application logic — is what makes a double-clicked
    button harmless."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_kind_active",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="status",
        ),
        CheckConstraint("created_by IN ('ui', 'schedule', 'cli')", name="created_by"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_by: Mapped[str] = mapped_column(String(10), default="ui")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done: Mapped[int] = mapped_column(default=0)
    total: Mapped[int | None]
    summary: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(String(200))
```

Add to `Message` (after `edited_by_user`):

```python
    # D22 voice critic — advisory only, never a gate.
    critic_verdict: Mapped[str | None] = mapped_column(String(20))
    critic_concerns: Mapped[dict | None] = mapped_column(JSON)
    critic_overall_note: Mapped[str | None] = mapped_column(String(200))
    # Reply classification (inbound rows only, spec §5).
    label: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None]
    rationale: Mapped[str | None] = mapped_column(String(300))
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Add to `Message.__table_args__`:

```python
        # Re-polling the same Gmail window must be free (spec §5).
        Index("uq_messages_gmail_message_id", "gmail_message_id", unique=True),
```

Add to `GmailCredential`:

```python
    last_history_id: Mapped[str | None] = mapped_column(String(40))
```

`JSON` comes from `sqlalchemy`; add it to the existing import line if absent.

- [ ] **Step 4: Write the migration**

`backend/alembic/versions/0003_phase2.py`, hand-written, `down_revision = "0002"`, `revision = "0003"`. Create the `jobs` table with both CHECKs and the partial unique index; `op.add_column` each new column; create `uq_messages_gmail_message_id`. `downgrade()` drops all of it in reverse.

- [ ] **Step 5: Verify the migration renders and matches the model**

Run: `cd backend && alembic upgrade head --sql > /tmp/0003.sql && python -m pytest tests/adapters/db/test_models_phase2.py -v`
Expected: SQL renders without error; 6 tests PASS. If a real Postgres is reachable, also run `alembic upgrade head`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/adapters/db/models.py backend/alembic/versions/0003_phase2.py backend/tests/adapters/db/test_models_phase2.py
git commit -m "feat: schema for jobs, critic verdicts and reply classification"
```

---

## Task 2: JobRepo and the RunJob use case

**Files:**
- Modify: `backend/app/adapters/db/repositories.py`
- Create: `backend/app/application/run_job.py`
- Test: `backend/tests/application/test_run_job.py`

**Interfaces:**
- Consumes: `Job` (Task 1).
- Produces: `JobRepo` with `upsert(**fields)`, `get(id)`, `list_recent(limit=50)`, `active(kind) -> Job | None`, `list_active()`. `RunJob(repo, clock, commit)` with `start(kind, created_by="ui") -> int`, `progress(job_id, done, total=None)`, `finish(job_id, summary)`, `fail(job_id, exc)`, `recover_stale(max_age_seconds)`. `JobAlreadyRunning(Exception)` carrying `.job_id`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_run_job.py
from datetime import datetime, timedelta, timezone

import pytest

from app.application.run_job import JobAlreadyRunning, RunJob

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


class FrozenClock:
    def __init__(self, now=NOW):
        self._now = now

    def now(self):
        return self._now


class FakeJobRepo:
    def __init__(self):
        self.rows, self._next = {}, 1

    def upsert(self, **fields):
        job_id = fields.pop("id", None) or self._next
        if job_id == self._next:
            self._next += 1
        row = self.rows.setdefault(job_id, type("J", (), {"id": job_id})())
        for k, v in fields.items():
            setattr(row, k, v)
        return row

    def get(self, job_id):
        return self.rows.get(job_id)

    def active(self, kind):
        return next((r for r in self.rows.values()
                     if r.kind == kind and r.status in ("queued", "running")), None)

    def list_active(self):
        return [r for r in self.rows.values() if r.status in ("queued", "running")]


def _runner(commits=None):
    return RunJob(FakeJobRepo(), FrozenClock(), commit=(commits.append if commits is not None else lambda: None))


def test_start_records_a_running_job():
    r = _runner()
    job_id = r.start("scrape")
    job = r.repo.get(job_id)
    assert job.kind == "scrape" and job.status == "running"
    assert job.started_at == NOW and job.created_by == "ui"


def test_a_second_start_of_the_same_kind_is_refused():
    r = _runner()
    first = r.start("scrape")
    with pytest.raises(JobAlreadyRunning) as exc:
        r.start("scrape")
    assert exc.value.job_id == first


def test_a_different_kind_may_run_concurrently():
    r = _runner()
    r.start("scrape")
    assert r.start("resolve")


def test_progress_updates_counts_and_commits():
    commits = []
    r = _runner(commits)
    job_id = r.start("draft")
    r.progress(job_id, done=3, total=10)
    job = r.repo.get(job_id)
    assert (job.done, job.total) == (3, 10)
    assert len(commits) >= 2


def test_finish_records_summary_and_frees_the_kind():
    r = _runner()
    job_id = r.start("sync")
    r.finish(job_id, "fetched 6214, matched 145")
    job = r.repo.get(job_id)
    assert job.status == "succeeded" and job.finished_at == NOW
    assert job.summary == "fetched 6214, matched 145"
    assert r.start("sync")            # the kind is free again


def test_fail_records_only_the_exception_type_name():
    r = _runner()
    job_id = r.start("resolve")
    r.fail(job_id, ValueError("key sk_live_secret is bad"))
    job = r.repo.get(job_id)
    assert job.status == "failed"
    assert job.error == "ValueError"
    assert "sk_live_secret" not in (job.error or "")


def test_recover_stale_fails_jobs_left_running_by_a_crash():
    r = _runner()
    job_id = r.start("scrape")
    r.repo.rows[job_id].started_at = NOW - timedelta(hours=3)
    recovered = r.recover_stale(max_age_seconds=3600)
    assert recovered == 1
    assert r.repo.get(job_id).status == "failed"
    assert r.repo.get(job_id).error == "interrupted"
    assert r.start("scrape")          # the index is released


def test_recover_stale_leaves_a_young_job_alone():
    r = _runner()
    r.start("scrape")
    assert r.recover_stale(max_age_seconds=3600) == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/application/test_run_job.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.application.run_job'`.

- [ ] **Step 3: Write the use case**

```python
# backend/app/application/run_job.py
"""Job bookkeeping (spec §4): one row per stage run, so the UI can show
real progress and a crash is visible rather than silent.

Adapter-free by construction — the caller injects a repo, a clock and a
commit callable, exactly as `ResolveEmails` does."""

from __future__ import annotations

from datetime import timedelta
from typing import Callable, Protocol


class JobAlreadyRunning(Exception):
    def __init__(self, kind: str, job_id: int):
        self.kind = kind
        self.job_id = job_id
        super().__init__(f"a {kind} job is already running (job {job_id})")


class _JobRepo(Protocol):
    def upsert(self, **fields): ...
    def get(self, job_id: int): ...
    def active(self, kind: str): ...
    def list_active(self) -> list: ...


class RunJob:
    def __init__(self, repo: _JobRepo, clock, commit: Callable[[], None]):
        self.repo = repo
        self._clock = clock
        self._commit = commit

    def start(self, kind: str, created_by: str = "ui") -> int:
        running = self.repo.active(kind)
        if running is not None:
            raise JobAlreadyRunning(kind, running.id)
        job = self.repo.upsert(
            kind=kind, status="running", created_by=created_by,
            started_at=self._clock.now(), done=0, total=None,
            summary=None, error=None, finished_at=None,
        )
        self._commit()
        return job.id

    def progress(self, job_id: int, done: int, total: int | None = None) -> None:
        fields = {"id": job_id, "done": done}
        if total is not None:
            fields["total"] = total
        self.repo.upsert(**fields)
        self._commit()

    def finish(self, job_id: int, summary: str = "") -> None:
        self.repo.upsert(id=job_id, status="succeeded",
                         finished_at=self._clock.now(), summary=summary[:500] or None)
        self._commit()

    def fail(self, job_id: int, exc: BaseException) -> None:
        # Type name only: an exception message can carry an API key.
        self.repo.upsert(id=job_id, status="failed",
                         finished_at=self._clock.now(), error=type(exc).__name__)
        self._commit()

    def recover_stale(self, max_age_seconds: int = 3600) -> int:
        """A job left `running` by a crashed API holds the partial unique
        index forever. Called at startup."""
        cutoff = self._clock.now() - timedelta(seconds=max_age_seconds)
        recovered = 0
        for job in self.repo.list_active():
            started = getattr(job, "started_at", None)
            if started is not None and started < cutoff:
                self.repo.upsert(id=job.id, status="failed",
                                 finished_at=self._clock.now(), error="interrupted")
                recovered += 1
        if recovered:
            self._commit()
        return recovered
```

- [ ] **Step 4: Add `JobRepo`**

In `repositories.py`, following the uniform pattern of the existing repos:

```python
class JobRepo:
    """Phase 2 (spec §4). Uniform `upsert(**fields)` like every other repo."""

    def __init__(self, session):
        self._session = session

    def upsert(self, **fields):
        job_id = fields.pop("id", None)
        row = self._session.get(Job, job_id) if job_id else None
        if row is None:
            row = Job(**fields)
            self._session.add(row)
        else:
            for key, value in fields.items():
                setattr(row, key, value)
        self._session.flush()
        return row

    def get(self, job_id: int):
        return self._session.get(Job, job_id)

    def active(self, kind: str):
        return self._session.scalars(
            select(Job).where(Job.kind == kind, Job.status.in_(("queued", "running")))
        ).first()

    def list_active(self) -> list[Job]:
        return list(self._session.scalars(
            select(Job).where(Job.status.in_(("queued", "running")))
        ))

    def list_recent(self, limit: int = 50) -> list[Job]:
        return list(self._session.scalars(
            select(Job).order_by(Job.id.desc()).limit(limit)
        ))
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/application/test_run_job.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/application/run_job.py backend/app/adapters/db/repositories.py backend/tests/application/test_run_job.py
git commit -m "feat: job bookkeeping with one active run per kind"
```

---

## Task 3: FastAPI skeleton, stats endpoint and the test harness

**Files:**
- Create: `backend/app/api/__init__.py`, `backend/app/api/main.py`, `backend/app/api/deps.py`, `backend/app/api/schemas.py`, `backend/app/api/routers/__init__.py`, `backend/app/api/routers/stats.py`
- Create: `backend/tests/api/__init__.py`, `backend/tests/api/conftest.py`, `backend/tests/api/test_stats.py`
- Modify: `backend/pyproject.toml` (add `fastapi>=0.115`, `uvicorn[standard]>=0.30`)

**Interfaces:**
- Produces: `create_app(repos_factory=None) -> FastAPI`; dependency `get_repos()` returning a `Repos` bundle; `GET /api/stats -> StatsOut{counts: dict[str,int], quota: QuotaOut, jobs: list[JobOut]}`. Test fixture `client` (a `TestClient` wired to `FakeRepos`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_stats.py
def test_stats_returns_funnel_counts_by_state(client, repos, two_founders):
    body = client.get("/api/stats").json()
    assert body["counts"]["new"] == 2


def test_stats_is_json_and_has_no_server_header_leak(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_unknown_route_is_404_not_a_traceback(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert "Traceback" not in r.text
```

```python
# backend/tests/api/conftest.py
"""API tests run against the same in-memory FakeRepos the application
tests use — no Postgres, no network, no env vars (global constraint)."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture
def client(repos):
    app = create_app(repos_factory=lambda: repos)
    with TestClient(app) as c:
        yield c
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/api/test_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api'`.

- [ ] **Step 3: Write the app factory and the stats router**

`deps.py` holds a `Repos` dataclass bundling every repo the routers need (founder, company, outreach, message, news, launch, job_posting, email_candidate, resume_profile, job) plus `session`. `create_app(repos_factory)` overrides the dependency when a factory is passed — that is the seam the tests use.

```python
# backend/app/api/main.py
"""FastAPI adapter (spec §3). Peer to `cli.py`: routers translate
DTO -> use case -> DTO and hold no business logic."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_repos, real_repos
from app.api.routers import stats

ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(repos_factory=None) -> FastAPI:
    app = FastAPI(title="YC Auto Apply", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"], allow_headers=["*"],
    )
    if repos_factory is not None:
        app.dependency_overrides[get_repos] = repos_factory
    app.include_router(stats.router, prefix="/api")
    return app


app = create_app()
```

The stats router counts outreach rows by state via `founder.list_by_state`, reads verifier quota from settings, and returns recent jobs.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/api/ -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify the whole suite is still green offline**

Run: `cd backend && env -i PATH="$PATH" python -m pytest -q`
Expected: PASS, no env vars needed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api backend/tests/api backend/pyproject.toml
git commit -m "feat: FastAPI adapter skeleton with funnel stats"
```

---

## Task 4: Review queue endpoints

**Files:**
- Create: `backend/app/api/routers/queue.py`
- Modify: `backend/app/api/main.py`, `backend/app/api/schemas.py`
- Test: `backend/tests/api/test_queue.py`

**Interfaces:**
- Consumes: `ReviewOutreach` (`pending()`, `load(id)`, `approve(id, now)`, `edit(id, subject, body) -> list[str]`, `reject(id)`, `redraft(id)`), `compose_wire_text`, `first_name`.
- Produces: `GET /api/queue -> list[QueueItemOut]`; `GET /api/queue/{id} -> QueueItemOut`; `POST /api/outreach/{id}/approve|reject|redraft -> {ok: true}`; `POST /api/outreach/{id}/edit {subject, body} -> {ok: true}` or **422** `{errors: [str]}`. `QueueItemOut` fields: `outreach_id, company_name, company_one_liner, founder_name, founder_title, to, subject, body, wire_text, word_count, source_kind, source_text, source_publication, last_error, critic_verdict, critic_note, sequence_step`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_queue.py
"""The review queue over HTTP. Same use case the CLI drives, so the two
reviewers can never disagree about what is approvable."""
from app.domain.compose import compose_wire_text, first_name
from app.domain.states import OutreachState as S


def test_queue_lists_pending_drafts_with_the_composed_preview(client, repos, pending_draft):
    items = client.get("/api/queue").json()
    assert len(items) == 1
    item = items[0]
    assert item["outreach_id"] == pending_draft.outreach.id
    assert item["to"] == pending_draft.founder.resolved_email
    # The preview is what will actually be sent, not the bare stored body.
    assert item["wire_text"] == compose_wire_text(
        first_name(pending_draft.founder.full_name), item["body"], ""
    )
    assert item["word_count"] == len(item["body"].split())


def test_queue_exposes_the_cited_source_so_a_claim_is_checkable(client, pending_draft):
    item = client.get("/api/queue").json()[0]
    assert item["source_kind"] == "news"
    assert item["source_text"]


def test_queue_is_ordered_by_rank_key(client, repos, two_pending_drafts):
    ids = [i["outreach_id"] for i in client.get("/api/queue").json()]
    assert ids == [d.outreach.id for d in two_pending_drafts]


def test_approve_moves_the_row_and_stamps_approved_at(client, repos, pending_draft):
    assert client.post(f"/api/outreach/{pending_draft.outreach.id}/approve").status_code == 200
    assert repos.outreach.get(pending_draft.outreach.id).state == S.APPROVED
    assert repos.message.get_outbound(pending_draft.outreach.id, 0).approved_at is not None


def test_approve_is_refused_for_a_flagged_unedited_draft(client, repos, flagged_draft):
    r = client.post(f"/api/outreach/{flagged_draft.outreach.id}/approve")
    assert r.status_code == 409
    assert repos.outreach.get(flagged_draft.outreach.id).state == S.PENDING_APPROVAL


def test_edit_returns_422_with_the_validators_own_messages(client, repos, pending_draft):
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/edit",
                    json={"subject": "hi", "body": "I hope this email finds you well."})
    assert r.status_code == 422
    assert any("banned" in e or "paragraph" in e or "intern" in e for e in r.json()["errors"])
    # Nothing persisted on a rejected edit.
    assert repos.message.get_outbound(pending_draft.outreach.id, 0).edited_by_user is False


def test_a_valid_edit_persists_and_marks_it_human_written(client, repos, pending_draft, good_body):
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/edit",
                    json={"subject": "Vahan software intern", "body": good_body})
    assert r.status_code == 200
    row = repos.message.get_outbound(pending_draft.outreach.id, 0)
    assert row.edited_by_user is True and row.body == good_body


def test_reject_discards_and_redraft_returns_it_to_drafting(client, repos, two_pending_drafts):
    a, b = two_pending_drafts
    client.post(f"/api/outreach/{a.outreach.id}/reject")
    client.post(f"/api/outreach/{b.outreach.id}/redraft")
    assert repos.outreach.get(a.outreach.id).state == S.DISCARDED
    assert repos.outreach.get(b.outreach.id).state == S.READY_TO_DRAFT


def test_actions_on_an_unknown_outreach_are_404(client):
    assert client.post("/api/outreach/9999/approve").status_code == 404
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/api/test_queue.py -v`
Expected: FAIL — `404` on every route (no queue router yet).

- [ ] **Step 3: Write the router**

Map the use case's exceptions to status codes in one place: `ValueError` from a missing row → 404; the flagged-approve refusal → 409; validator errors from `edit` → 422 with `{"errors": [...]}`. Commit the session after every successful action, never after a refused one.

```python
# backend/app/api/routers/queue.py
@router.post("/outreach/{outreach_id}/approve")
def approve(outreach_id: int, repos=Depends(get_repos)):
    use_case = ReviewOutreach(_review_repos(repos))
    try:
        use_case.approve(outreach_id, _UTCClock().now())
    except KeyError:
        raise HTTPException(404, "no such outreach")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    repos.session.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/api/test_queue.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/tests/api/test_queue.py
git commit -m "feat: review queue endpoints over the same use case as the CLI"
```

---

## Task 5: Manual-email, send and contacts endpoints

**Files:**
- Create: `backend/app/api/routers/manual.py`, `send.py`, `contacts.py`
- Test: `backend/tests/api/test_manual.py`, `backend/tests/api/test_send.py`, `backend/tests/api/test_contacts.py`

**Interfaces:**
- Consumes: `ResolveManualEmail` (`pending()`, `set_email(id, address)`, `give_up(id)`), `SendApproved(sender, clock, repos, signature, attachment_path).run(cap, limit)`.
- Produces: `GET /api/manual -> list[ManualItemOut{outreach_id, founder_name, company_name, domain, linkedin_url, candidates:[{address,verdict}], published_hint}]`; `POST /api/outreach/{id}/email {address} | {give_up: true}`; `POST /api/send {limit?} -> SendResultOut{sent, skipped_cap, skipped_not_reservable, needs_manual_check, errors}`; `GET /api/contacts?state=&hiring=&batch=&email_status= -> list[ContactOut]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/api/test_manual.py
def test_manual_lists_founders_with_the_candidates_already_tried(client, manual_founder):
    item = client.get("/api/manual").json()[0]
    assert item["outreach_id"] == manual_founder.outreach.id
    assert item["candidates"][0]["verdict"] == "catch_all"


def test_setting_an_address_moves_the_row_to_drafting(client, repos, manual_founder):
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email",
                    json={"address": "Pratish@OneFin.in"})
    assert r.status_code == 200
    row = repos.outreach.get(manual_founder.outreach.id)
    assert row.state.value == "ready_to_draft"
    assert row.resolved_email == "pratish@onefin.in"     # normalised


def test_a_malformed_address_is_422_and_changes_nothing(client, repos, manual_founder):
    r = client.post(f"/api/outreach/{manual_founder.outreach.id}/email", json={"address": "not-an-email"})
    assert r.status_code == 422
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "needs_manual_email"


def test_giving_up_marks_it_unresolved(client, repos, manual_founder):
    client.post(f"/api/outreach/{manual_founder.outreach.id}/email", json={"give_up": True})
    assert repos.outreach.get(manual_founder.outreach.id).state.value == "unresolved"
```

```python
# backend/tests/api/test_send.py
def test_send_sends_approved_rows_and_reports_counts(client, repos, approved_draft, fake_sender):
    body = client.post("/api/send", json={"limit": 5}).json()
    assert body["sent"] == 1
    assert repos.outreach.get(approved_draft.outreach.id).state.value == "sent"


def test_send_respects_the_daily_cap(client, repos, five_approved, fake_sender):
    body = client.post("/api/send", json={}).json()
    assert body["sent"] + body["skipped_cap"] == 5


def test_send_never_sends_a_pending_draft(client, repos, pending_draft, fake_sender):
    assert client.post("/api/send", json={}).json()["sent"] == 0
    assert fake_sender.sent == []
```

```python
# backend/tests/api/test_contacts.py
def test_contacts_lists_every_company_and_founder(client, two_founders):
    assert len(client.get("/api/contacts").json()) == 2


def test_contacts_filter_by_state(client, two_founders):
    rows = client.get("/api/contacts?state=new").json()
    assert all(r["state"] == "new" for r in rows)


def test_hiring_filter_is_a_view_control_not_a_gate(client, founder_with_senior_posting):
    # D19: non-hiring companies are still listed by default.
    assert client.get("/api/contacts").json()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/api/test_manual.py tests/api/test_send.py tests/api/test_contacts.py -v`
Expected: FAIL — 404 on every route.

- [ ] **Step 3: Write the three routers**

`send.py` builds a `GmailSender` from settings (refusing with 400 and a clear message when any of `SIGNATURE`, `RESUME_PATH`, `GMAIL_SENDER_ADDRESS`, `GMAIL_CLIENT_SECRETS_PATH` is unset, or no refresh token is stored — the same guards `cli.py` applies, extracted into `app/api/deps.py::require_send_config()` so both callers share one rule). In tests the sender is injected through the repos bundle.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/api/ -v`
Expected: PASS (10 new tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/tests/api
git commit -m "feat: manual-email, send and contacts endpoints"
```

---

## Task 6: Job endpoints and the background runner

**Files:**
- Create: `backend/app/api/jobs.py`, `backend/app/api/routers/jobs.py`
- Modify: `backend/app/api/main.py` (startup recovery)
- Test: `backend/tests/api/test_jobs.py`

**Interfaces:**
- Consumes: `RunJob`, `JobAlreadyRunning` (Task 2).
- Produces: `JOB_KINDS: dict[str, Callable[[Session, ProgressFn], str]]` — each entry runs one stage with its own session and returns a summary line; `start_job(kind, created_by, session_factory, runner_factory) -> int`; `POST /api/jobs/{kind} -> {job_id}` (409 `{job_id}` when that kind runs); `GET /api/jobs -> list[JobOut{id, kind, status, done, total, summary, error, started_at, finished_at}]`; `recover_stale_jobs(app)` on startup.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_jobs.py
"""Jobs are the only way the UI runs a stage. The thread is real; the
stage function is a fake, so no network or DB is involved."""
import threading

import pytest


def test_starting_a_job_returns_its_id_and_runs_the_stage(client, job_probe):
    body = client.post("/api/jobs/sync").json()
    assert body["job_id"]
    job_probe.finished.wait(timeout=5)
    assert job_probe.ran == ["sync"]


def test_a_second_start_of_the_same_kind_is_409_with_the_running_id(client, blocking_job):
    first = client.post("/api/jobs/scrape").json()["job_id"]
    r = client.post("/api/jobs/scrape")
    assert r.status_code == 409
    assert r.json()["job_id"] == first
    blocking_job.release()


def test_an_unknown_kind_is_404(client):
    assert client.post("/api/jobs/nonsense").status_code == 404


def test_job_list_shows_progress_while_running(client, blocking_job):
    client.post("/api/jobs/scrape")
    blocking_job.report(3, 10)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "scrape")
    assert (row["done"], row["total"], row["status"]) == (3, 10, "running")
    blocking_job.release()


def test_a_failing_stage_records_the_type_name_only(client, failing_job):
    client.post("/api/jobs/resolve")
    failing_job.finished.wait(timeout=5)
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "resolve")
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError"
    assert "secret" not in (row["error"] or "")


def test_a_failed_job_frees_the_kind_for_a_retry(client, failing_job):
    client.post("/api/jobs/resolve")
    failing_job.finished.wait(timeout=5)
    assert client.post("/api/jobs/resolve").status_code == 200


def test_the_job_thread_never_uses_the_request_session(client, job_probe):
    client.post("/api/jobs/sync")
    job_probe.finished.wait(timeout=5)
    assert job_probe.sessions_opened == 1     # its own, not the request's


def test_startup_marks_a_job_left_running_by_a_crash_as_interrupted(stale_job_client):
    row = next(j for j in stale_job_client.get("/api/jobs").json() if j["kind"] == "scrape")
    assert row["status"] == "failed" and row["error"] == "interrupted"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/api/test_jobs.py -v`
Expected: FAIL — 404 on `/api/jobs/sync`.

- [ ] **Step 3: Write the runner and the router**

```python
# backend/app/api/jobs.py
"""Background stage execution (spec §4).

A job thread opens its OWN session: sharing the request's would keep a web
request alive for the length of a four-minute scrape and expose the same
Session to two threads."""

import logging
import threading

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int | None], None]


def _sync(session, progress) -> str:
    from app.adapters.db.repositories import CompanyRepo
    from app.adapters.yc_oss import YCOSSDirectory
    from app.application.sync_companies import SyncCompanies

    result = SyncCompanies(YCOSSDirectory(), CompanyRepo(session)).run()
    session.commit()
    return f"fetched {result.fetched}, matched {result.matched}, upserted {result.upserted}"


JOB_KINDS = {
    "sync": _sync,
    "scrape": _scrape,          # reports progress per company
    "resolve": _resolve,
    "draft": _draft,
    "poll-replies": _poll_replies,            # Task 9
    "classify-replies": _classify_replies,    # Task 10
    "schedule-follow-ups": _schedule_follow_ups,   # Task 11
}


def start_job(kind, created_by, session_factory, clock) -> int:
    if kind not in JOB_KINDS:
        raise KeyError(kind)
    session = session_factory()
    runner = RunJob(JobRepo(session), clock, commit=session.commit)
    job_id = runner.start(kind, created_by)      # raises JobAlreadyRunning
    session.close()

    def _run():
        own = session_factory()
        own_runner = RunJob(JobRepo(own), clock, commit=own.commit)
        try:
            summary = JOB_KINDS[kind](own, lambda d, t=None: own_runner.progress(job_id, d, t))
            own_runner.finish(job_id, summary)
        except BaseException as exc:
            log.warning("job %s failed: %s", kind, type(exc).__name__)
            own_runner.fail(job_id, exc)
        finally:
            own.close()

    threading.Thread(target=_run, name=f"job-{kind}", daemon=True).start()
    return job_id
```

Startup hook in `main.py` calls `RunJob(...).recover_stale(3600)` once.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/api/test_jobs.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/tests/api/test_jobs.py
git commit -m "feat: run pipeline stages as background jobs with progress"
```

---

## Task 7: Gmail read scope and the reader adapter

**Files:**
- Create: `backend/app/adapters/gmail_reader.py`
- Modify: `backend/app/cli.py` (`gmail-auth` scopes), `backend/app/adapters/db/repositories.py` (`GmailCredentialRepo.set_history_id`)
- Test: `backend/tests/adapters/test_gmail_reader.py`

**Interfaces:**
- Produces: `GmailReader(credential_repo, account, *, timeout=30, _sleep=time.sleep, _client=None)` with `history_since(history_id) -> tuple[list[str], str]` (new message ids, new history id), `recent_message_ids(query="newer_than:14d") -> list[str]`, `get_message(message_id) -> InboundMessage{gmail_message_id, gmail_thread_id, from_address, subject, body_text, received_at}`, `current_history_id() -> str`. `GmailHistoryExpired(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/adapters/test_gmail_reader.py
"""Reading replies (spec §5). All HTTP mocked with respx; the access token
and client secret must never appear in an exception or a log record."""
import httpx
import pytest
import respx

from app.adapters.gmail_reader import GmailHistoryExpired, GmailReader

HISTORY = "https://gmail.googleapis.com/gmail/v1/users/me/history"
MESSAGES = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


@respx.mock
def test_history_returns_new_message_ids_and_the_new_cursor(reader):
    respx.get(HISTORY).mock(return_value=httpx.Response(200, json={
        "history": [{"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]},
                    {"messagesAdded": [{"message": {"id": "m2", "threadId": "t1"}}]}],
        "historyId": "555",
    }))
    ids, new_cursor = reader.history_since("500")
    assert ids == ["m1", "m2"] and new_cursor == "555"


@respx.mock
def test_history_pages_through_next_page_token(reader):
    respx.get(HISTORY).mock(side_effect=[
        httpx.Response(200, json={"history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                                  "nextPageToken": "p2", "historyId": "500"}),
        httpx.Response(200, json={"history": [{"messagesAdded": [{"message": {"id": "m2"}}]}],
                                  "historyId": "600"}),
    ])
    ids, cursor = reader.history_since("400")
    assert ids == ["m1", "m2"] and cursor == "600"


@respx.mock
def test_an_expired_history_id_raises_so_the_caller_can_fall_back(reader):
    respx.get(HISTORY).mock(return_value=httpx.Response(404, json={"error": {"message": "not found"}}))
    with pytest.raises(GmailHistoryExpired):
        reader.history_since("1")


@respx.mock
def test_recent_message_ids_uses_the_newer_than_query(reader):
    route = respx.get(MESSAGES).mock(return_value=httpx.Response(200, json={
        "messages": [{"id": "m9", "threadId": "t9"}]}))
    assert reader.recent_message_ids() == ["m9"]
    assert "newer_than" in str(route.calls[0].request.url)


@respx.mock
def test_get_message_decodes_the_plain_text_body_and_headers(reader):
    import base64
    body = base64.urlsafe_b64encode("Sure, send it over.".encode()).decode()
    respx.get(f"{MESSAGES}/m1").mock(return_value=httpx.Response(200, json={
        "id": "m1", "threadId": "t1", "internalDate": "1789000000000",
        "payload": {"headers": [{"name": "From", "value": "Pratish <pratish@onefin.in>"},
                                {"name": "Subject", "value": "Re: OneFin intern"}],
                    "mimeType": "text/plain", "body": {"data": body}},
    }))
    msg = reader.get_message("m1")
    assert msg.gmail_thread_id == "t1"
    assert msg.from_address == "pratish@onefin.in"
    assert "send it over" in msg.body_text
    assert msg.received_at.year == 2026


@respx.mock
def test_get_message_prefers_the_text_part_of_a_multipart_reply(reader):
    import base64
    text = base64.urlsafe_b64encode(b"plain version").decode()
    html = base64.urlsafe_b64encode(b"<p>html version</p>").decode()
    respx.get(f"{MESSAGES}/m2").mock(return_value=httpx.Response(200, json={
        "id": "m2", "threadId": "t1", "internalDate": "1789000000000",
        "payload": {"headers": [{"name": "From", "value": "a@b.com"}], "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/html", "body": {"data": html}},
                              {"mimeType": "text/plain", "body": {"data": text}}]},
    }))
    assert reader.get_message("m2").body_text == "plain version"


@respx.mock
def test_a_401_never_carries_the_token(reader):
    respx.get(HISTORY).mock(return_value=httpx.Response(401, json={"error": {"message": "bad token"}}))
    with pytest.raises(Exception) as exc_info:
        reader.history_since("1")
    assert "ya29" not in str(exc_info.value)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/adapters/test_gmail_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.gmail_reader'`.

- [ ] **Step 3: Write the reader**

Reuse `GmailSender`'s refresh-token exchange (extract it to a shared `_access_token(credential)` helper in `gmail.py` and import it, rather than duplicating). Raise `GmailError`-style exceptions carrying stage and status only.

- [ ] **Step 4: Widen the auth scope**

In `cli.py`, `gmail-auth` requests both scopes:

```python
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
```

Print a clear note that an existing token must be re-authorised once.

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/adapters/test_gmail_reader.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/adapters/gmail_reader.py backend/app/adapters/gmail.py backend/app/cli.py backend/tests/adapters/test_gmail_reader.py
git commit -m "feat: gmail reader over the history API with read-only scope"
```

---

## Task 8: PollReplies

**Files:**
- Create: `backend/app/application/poll_replies.py`
- Modify: `backend/app/adapters/db/repositories.py` (`MessageRepo.get_by_gmail_id`, `list_by_thread_id`, `OutreachRepo.get_by_thread_id`)
- Test: `backend/tests/application/test_poll_replies.py`

**Interfaces:**
- Consumes: `GmailReader` (Task 7).
- Produces: `PollReplies(reader, repos, clock).run() -> PollResult{fetched, stored, bounced, skipped}`; `PollRepos{outreach, message, founder, credential, session}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_poll_replies.py
"""Replies land as inbound `messages` rows. Re-polling must be free, an
autoresponder must not end an outreach, and a bounce must be visible."""
import pytest

from app.application.poll_replies import PollReplies
from app.domain.states import OutreachState as S


def test_a_reply_on_a_thread_we_sent_is_stored_as_inbound(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1",
                    sender="pratish@onefin.in", body="Sure, send it over.")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.stored == 1
    row = repos.message.list_by_outreach(sent_outreach.outreach.id)[-1]
    assert row.direction == "inbound" and row.gmail_message_id == "m1"
    assert row.label is None            # classification is a separate step


def test_mail_on_an_unknown_thread_is_ignored(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id="not-ours", message_id="x1", sender="spam@x.com", body="hi")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.stored == 0 and result.skipped == 1


def test_polling_the_same_message_twice_stores_it_once(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    p = PollReplies(fake_reader, _repos(repos), _clock())
    p.run()
    fake_reader.rewind()                 # same window again
    assert p.run().stored == 0


def test_an_expired_history_id_falls_back_to_a_recent_window(repos, sent_outreach, fake_reader):
    fake_reader.expire_history()
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    assert PollReplies(fake_reader, _repos(repos), _clock()).run().stored == 1
    assert fake_reader.used_fallback is True


def test_the_history_cursor_is_advanced_and_persisted(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="ok")
    PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert repos.credential.get_by_account_email("me@gmail.com").last_history_id == fake_reader.history_id


def test_a_bounce_marks_the_outreach_and_is_never_classified(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="b1",
                    sender="mailer-daemon@googlemail.com", body="Address not found")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.bounced == 1
    assert repos.outreach.get(sent_outreach.outreach.id).state == S.BOUNCED
    assert repos.message.list_by_outreach(sent_outreach.outreach.id)[-1].label == "BOUNCE"


def test_polling_does_not_change_state_on_its_own(repos, sent_outreach, fake_reader):
    # Classification, not polling, is what moves a row to REPLIED.
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert repos.outreach.get(sent_outreach.outreach.id).state == S.SENT
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/application/test_poll_replies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.application.poll_replies'`.

- [ ] **Step 3: Write the use case**

Order: read cursor → `history_since` (on `GmailHistoryExpired` fall back to `recent_message_ids`) → for each id, skip if `get_by_gmail_id` exists → fetch → match thread → bounce check by sender → insert inbound row → commit per message → persist the new cursor last, so a crash re-reads rather than skips.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/application/test_poll_replies.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/poll_replies.py backend/app/adapters/db/repositories.py backend/tests/application/test_poll_replies.py
git commit -m "feat: poll gmail for replies, dedupe by message id, flag bounces"
```

---

## Task 9: ClassifyReply

**Files:**
- Create: `backend/app/application/classify_reply.py`
- Modify: `backend/app/adapters/gemini.py`, `backend/app/adapters/sarvam.py` (classifier prompt + schema), `backend/app/ports/__init__.py` (`ReplyClassifier` protocol)
- Test: `backend/tests/application/test_classify_reply.py`, `backend/tests/adapters/test_classifier_adapters.py`

**Interfaces:**
- Produces: `ReplyClassifier` port with `classify(context: dict) -> dict{label, confidence, rationale}`; `ClassifyReply(classifier, repos, clock).run(limit=None) -> ClassifyResult{classified, replied, ignored, errors}`; `LABELS = ("INTERESTED","REJECTED","REFERRAL","AUTO_REPLY","OUT_OF_OFFICE","UNCLEAR")` in `app/domain/replies.py` with `cancels_sequence(label) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_replies.py
import pytest

from app.domain.replies import LABELS, cancels_sequence


def test_the_six_spec_labels_exist():
    assert set(LABELS) == {"INTERESTED", "REJECTED", "REFERRAL",
                           "AUTO_REPLY", "OUT_OF_OFFICE", "UNCLEAR"}


@pytest.mark.parametrize("label", ["INTERESTED", "REJECTED", "REFERRAL", "UNCLEAR"])
def test_human_labels_cancel_the_sequence(label):
    assert cancels_sequence(label) is True


@pytest.mark.parametrize("label", ["AUTO_REPLY", "OUT_OF_OFFICE"])
def test_machine_labels_never_cancel_the_sequence(label):
    # An autoresponder must not be able to end an outreach (spec §5).
    assert cancels_sequence(label) is False
```

```python
# backend/tests/application/test_classify_reply.py
from app.application.classify_reply import ClassifyReply
from app.domain.states import OutreachState as S


def test_an_interested_reply_moves_the_row_to_replied_and_stops_follow_ups(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "INTERESTED", "confidence": 0.9, "rationale": "asks to talk"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    outreach = repos.outreach.get(unlabelled_reply.outreach.id)
    assert outreach.state == S.REPLIED and outreach.next_action_at is None


def test_an_out_of_office_is_recorded_but_changes_nothing(repos, unlabelled_reply, fake_classifier):
    before = repos.outreach.get(unlabelled_reply.outreach.id).next_action_at
    fake_classifier.next = {"label": "OUT_OF_OFFICE", "confidence": 0.95, "rationale": "auto"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    outreach = repos.outreach.get(unlabelled_reply.outreach.id)
    assert outreach.state == S.SENT and outreach.next_action_at == before


def test_the_label_confidence_and_rationale_are_all_persisted(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "REJECTED", "confidence": 0.8, "rationale": "not hiring interns"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    row = repos.message.get(unlabelled_reply.message.id)
    assert (row.label, row.confidence, row.rationale) == ("REJECTED", 0.8, "not hiring interns")
    assert row.classified_at is not None


def test_an_already_labelled_reply_is_not_reclassified(repos, labelled_reply, fake_classifier):
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    assert fake_classifier.calls == []


def test_an_unknown_label_from_the_model_becomes_unclear(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "MAYBE", "confidence": 0.5, "rationale": "?"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    assert repos.message.get(unlabelled_reply.message.id).label == "UNCLEAR"


def test_a_classifier_failure_leaves_the_reply_unlabelled_and_visible(repos, unlabelled_reply, exploding_classifier):
    result = ClassifyReply(exploding_classifier, _repos(repos), _clock()).run()
    assert result.errors == 1
    assert repos.message.get(unlabelled_reply.message.id).label is None


def test_the_classifier_sees_the_thread_not_just_the_reply(repos, unlabelled_reply, fake_classifier):
    fake_classifier.next = {"label": "UNCLEAR", "confidence": 0.4, "rationale": "short"}
    ClassifyReply(fake_classifier, _repos(repos), _clock()).run()
    ctx = fake_classifier.calls[0]
    assert ctx["our_message"] and ctx["reply_body"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/domain/test_replies.py tests/application/test_classify_reply.py -v`
Expected: FAIL — missing modules.

- [ ] **Step 3: Write the domain rules, the use case and the adapters**

`app/domain/replies.py` is pure. Adapter prompt states: classify the founder's reply to a cold internship enquiry into exactly one label; judge intent, never facts; `rationale` at most 25 words. Schema mirrors the resume-claims schema shape already used by both providers.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/domain/test_replies.py tests/application/test_classify_reply.py tests/adapters/test_classifier_adapters.py -v`
Expected: PASS (3 + 7 + adapter tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/replies.py backend/app/application/classify_reply.py backend/app/adapters backend/app/ports backend/tests
git commit -m "feat: classify replies into the six spec labels with a rationale"
```

---

## Task 10: Thread endpoint and label correction

**Files:**
- Create: `backend/app/api/routers/thread.py`
- Test: `backend/tests/api/test_thread.py`

**Interfaces:**
- Produces: `GET /api/outreach/{id}/thread -> ThreadOut{outreach_id, company_name, founder_name, to, state, gmail_url, messages: [MessageOut{id, direction, sequence_step, subject, body, sent_at, received_at, label, confidence, rationale, corrected}]}`; `POST /api/messages/{id}/label {label} -> {ok}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_thread.py
def test_thread_returns_outbound_and_inbound_in_time_order(client, thread_with_reply):
    body = client.get(f"/api/outreach/{thread_with_reply.outreach.id}/thread").json()
    directions = [m["direction"] for m in body["messages"]]
    assert directions == ["outbound", "inbound"]


def test_thread_exposes_a_deep_link_to_gmail(client, thread_with_reply):
    body = client.get(f"/api/outreach/{thread_with_reply.outreach.id}/thread").json()
    assert thread_with_reply.thread_id in body["gmail_url"]


def test_a_label_can_be_corrected_by_hand(client, repos, thread_with_reply):
    reply_id = thread_with_reply.reply.id
    r = client.post(f"/api/messages/{reply_id}/label", json={"label": "REFERRAL"})
    assert r.status_code == 200
    assert repos.message.get(reply_id).label == "REFERRAL"


def test_correcting_to_a_human_label_cancels_follow_ups(client, repos, thread_with_auto_reply):
    client.post(f"/api/messages/{thread_with_auto_reply.reply.id}/label", json={"label": "INTERESTED"})
    outreach = repos.outreach.get(thread_with_auto_reply.outreach.id)
    assert outreach.state.value == "replied" and outreach.next_action_at is None


def test_an_invalid_label_is_422(client, thread_with_reply):
    r = client.post(f"/api/messages/{thread_with_reply.reply.id}/label", json={"label": "GREAT"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/api/test_thread.py -v`
Expected: FAIL — 404.

- [ ] **Step 3: Write the router**

A correction reuses the same transition rules as `ClassifyReply` — extract them into `ClassifyReply.apply_label(message, label)` so the automatic and manual paths cannot drift.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/api/test_thread.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/tests/api/test_thread.py
git commit -m "feat: thread timeline with hand-correctable classifications"
```

---

## Task 11: Follow-up scheduling

**Files:**
- Create: `backend/app/application/schedule_follow_ups.py`
- Modify: `backend/app/application/send_approved.py` (lift the `sequence_step` guard), `backend/app/adapters/db/repositories.py` (`OutreachRepo.list_due`)
- Test: `backend/tests/application/test_schedule_follow_ups.py`, `backend/tests/application/test_send_approved.py` (replace the guard tests)

**Interfaces:**
- Produces: `ScheduleFollowUps(repos, clock, day_1=5, day_2=12).run() -> FollowUpResult{scheduled, closed}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_schedule_follow_ups.py
"""Follow-ups at +5 and +12 days (spec §6). Nothing auto-sends: a scheduled
follow-up re-enters the same approval queue as a first email."""
from datetime import timedelta

from app.application.schedule_follow_ups import ScheduleFollowUps
from app.domain.states import OutreachState as S


def test_a_due_first_email_is_scheduled_for_follow_up_one(repos, sent_five_days_ago, clock):
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.scheduled == 1
    outreach = repos.outreach.get(sent_five_days_ago.outreach.id)
    assert outreach.state == S.READY_TO_DRAFT and outreach.sequence_step == 1


def test_a_row_not_yet_due_is_left_alone(repos, sent_two_days_ago, clock):
    assert ScheduleFollowUps(_repos(repos), clock).run().scheduled == 0
    assert repos.outreach.get(sent_two_days_ago.outreach.id).state == S.SENT


def test_after_step_two_the_row_closes(repos, sent_step_two_due, clock):
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.closed == 1
    assert repos.outreach.get(sent_step_two_due.outreach.id).state == S.CLOSED


def test_a_replied_row_is_never_followed_up(repos, replied_outreach, clock):
    assert ScheduleFollowUps(_repos(repos), clock).run().scheduled == 0


def test_a_bounced_row_is_never_followed_up(repos, bounced_outreach, clock):
    assert ScheduleFollowUps(_repos(repos), clock).run().scheduled == 0


def test_the_intervals_come_from_settings_not_constants(repos, sent_five_days_ago, clock):
    # day_1=30 means a 5-day-old row is not due.
    assert ScheduleFollowUps(_repos(repos), clock, day_1=30).run().scheduled == 0
```

```python
# in backend/tests/application/test_send_approved.py — replaces the R-C guard test
def test_a_follow_up_step_can_now_be_sent(repos, approved_follow_up, fake_sender, clock):
    SendApproved(fake_sender, clock, _repos(repos), signature="S").run(cap=10)
    assert len(fake_sender.sent) == 1
    row = repos.message.get_outbound(approved_follow_up.outreach.id, 1)
    assert row.sent_at is not None


def test_reserve_before_send_still_holds_per_step(repos, approved_follow_up, fake_sender, clock):
    # The step-1 row reserves independently of the step-0 row.
    assert repos.message.reserve_for_send(approved_follow_up.message.id, clock.now()) == 1
    assert repos.message.reserve_for_send(approved_follow_up.message.id, clock.now()) == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/application/test_schedule_follow_ups.py -v`
Expected: FAIL — module missing; the `send_approved` additions fail on the `NotImplementedError` guard.

- [ ] **Step 3: Write the use case and lift the guard**

Remove the `sequence_step != 0` `NotImplementedError` from `SendApproved` and the `followup_day_1` hardcode; read intervals from the `settings` row with the constructor defaults as fallback.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/application/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/application backend/app/adapters/db/repositories.py backend/tests
git commit -m "feat: schedule follow-ups at +5 and +12 days, close after step two"
```

---

## Task 12: Follow-up drafting — thread history and the shorter-than-previous rule

**Files:**
- Modify: `backend/app/domain/validation.py`, `backend/app/application/draft_outreach.py`, `backend/app/adapters/gemini.py` (follow-up prompt)
- Test: `backend/tests/domain/test_validation_followup.py`, `backend/tests/application/test_draft_followup.py`

**Interfaces:**
- Produces: `validate_style(draft, previous_body=None)` enforcing the shrink rule; `FOLLOW_UP_WORD_TARGETS = {1: 60, 2: 40}`; `build_draft_prompt` accepts `thread_history: list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_validation_followup.py
"""A follow-up earns less attention than the first email did, not more
(spec §6). The rule is deterministic so the model cannot argue with it."""
from app.domain.entities import Draft
from app.domain.validation import FOLLOW_UP_WORD_TARGETS, validate_style

FIRST = ("Vahan routes blue-collar hiring over voice.\n\n"
         "I built a scheduler in Python that cut queue wait by half.\n\n"
         "Would you take a software engineering intern?")


def _draft(body, step=1):
    return Draft(subject="Vahan intern follow up", body=body,
                 research_source_type="news", research_source_id=1, sequence_step=step)


def test_a_follow_up_longer_than_the_first_email_is_rejected():
    longer = FIRST + "\n\nAlso I have more to say about all of this at length."
    errs = validate_style(_draft(longer), previous_body=FIRST)
    assert any("shorter" in e for e in errs)


def test_a_shorter_follow_up_passes():
    shorter = "Following up on the note about voice routing.\n\nStill keen to intern with you."
    assert validate_style(_draft(shorter), previous_body=FIRST) == []


def test_the_step_word_targets_shrink():
    assert FOLLOW_UP_WORD_TARGETS[1] > FOLLOW_UP_WORD_TARGETS[2]


def test_a_follow_up_that_restates_the_first_email_is_rejected():
    errs = validate_style(_draft(FIRST), previous_body=FIRST)
    assert errs


def test_a_first_email_is_not_subject_to_the_shrink_rule():
    assert validate_style(_draft(FIRST, step=0), previous_body=None) == []
```

```python
# backend/tests/application/test_draft_followup.py
def test_a_follow_up_prompt_carries_the_thread_history(repos, ready_follow_up, fake_drafter):
    DraftOutreach(fake_drafter, _repos(repos)).run(limit=1)
    ctx = fake_drafter.calls[0]
    assert ctx["sequence_step"] == 1
    assert ctx["thread_history"][0]["body"]


def test_a_follow_up_draft_lands_in_the_same_approval_queue(repos, ready_follow_up, fake_drafter):
    DraftOutreach(fake_drafter, _repos(repos)).run(limit=1)
    assert repos.outreach.get(ready_follow_up.outreach.id).state.value == "pending_approval"


def test_the_follow_up_row_is_written_at_its_own_step(repos, ready_follow_up, fake_drafter):
    DraftOutreach(fake_drafter, _repos(repos)).run(limit=1)
    assert repos.message.get_outbound(ready_follow_up.outreach.id, 1) is not None
    assert repos.message.get_outbound(ready_follow_up.outreach.id, 0).body   # step 0 untouched
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && python -m pytest tests/domain/test_validation_followup.py tests/application/test_draft_followup.py -v`
Expected: FAIL — `FOLLOW_UP_WORD_TARGETS` missing; `thread_history` absent from the context.

- [ ] **Step 3: Implement**

`validate_style` gains: when `previous_body` is given, reject unless the new body has fewer words; reuse the existing overlap helper to reject near-restatement. `DraftOutreach` passes `sequence_step` and `thread_history` (prior outbound plus any inbound bodies) and calls `validate_draft(..., previous_body=...)`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/domain tests/application -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/validation.py backend/app/application/draft_outreach.py backend/app/adapters/gemini.py backend/tests
git commit -m "feat: follow-up drafts carry thread history and must be shorter"
```

---

## Task 13: The voice critic (D22)

**Files:**
- Create: `backend/app/application/critique_draft.py`
- Modify: `backend/app/adapters/gemini.py`, `backend/app/adapters/sarvam.py`, `backend/app/ports/__init__.py`, `backend/app/application/draft_outreach.py`
- Test: `backend/tests/application/test_critique_draft.py`

**Interfaces:**
- Produces: `Critic` port with `critique(draft: dict) -> dict{verdict, concerns, overall_note}`; `DraftOutreach(..., critic=None)` — when a critic is injected it runs **after** the deterministic validator passes.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_critique_draft.py
"""The critic is advisory (D22): it can never block a send and never
approve one. It only ever adds information for the human."""
PASS = {"verdict": "PASS", "concerns": [], "overall_note": "specific and short"}
REVISE = {"verdict": "NEEDS_REVISION",
          "concerns": [{"span": "excited about your work", "issue": "could be any company"}],
          "overall_note": "opener is generic"}


def test_a_pass_verdict_is_stored_with_the_draft(repos, ready_founder, fake_drafter, fake_critic):
    fake_critic.next = PASS
    DraftOutreach(fake_drafter, _repos(repos), critic=fake_critic).run(limit=1)
    row = repos.message.get_outbound(ready_founder.outreach.id, 0)
    assert row.critic_verdict == "PASS" and row.critic_overall_note


def test_needs_revision_triggers_exactly_one_redraft(repos, ready_founder, fake_drafter, fake_critic):
    fake_critic.queue = [REVISE, PASS]
    DraftOutreach(fake_drafter, _repos(repos), critic=fake_critic).run(limit=1)
    assert len(fake_drafter.calls) == 2
    assert fake_drafter.calls[1]["critic_concerns"]


def test_a_second_needs_revision_still_reaches_the_human(repos, ready_founder, fake_drafter, fake_critic):
    fake_critic.queue = [REVISE, REVISE]
    DraftOutreach(fake_drafter, _repos(repos), critic=fake_critic).run(limit=1)
    assert len(fake_drafter.calls) == 2                 # never more than one critic retry
    assert repos.outreach.get(ready_founder.outreach.id).state.value == "pending_approval"
    assert repos.message.get_outbound(ready_founder.outreach.id, 0).critic_verdict == "NEEDS_REVISION"


def test_the_critic_redraft_is_revalidated_deterministically(repos, ready_founder, drafter_bad_then_good, fake_critic):
    # A tone fix must not be allowed to smuggle in a banned phrase.
    fake_critic.queue = [REVISE, PASS]
    result = DraftOutreach(drafter_bad_then_good, _repos(repos), critic=fake_critic).run(limit=1)
    assert result.manual_write + result.drafted == 1


def test_a_critic_failure_never_blocks_the_draft(repos, ready_founder, fake_drafter, exploding_critic):
    DraftOutreach(fake_drafter, _repos(repos), critic=exploding_critic).run(limit=1)
    assert repos.outreach.get(ready_founder.outreach.id).state.value == "pending_approval"


def test_without_a_critic_behaviour_is_unchanged(repos, ready_founder, fake_drafter):
    DraftOutreach(fake_drafter, _repos(repos)).run(limit=1)
    assert repos.message.get_outbound(ready_founder.outreach.id, 0).critic_verdict is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/application/test_critique_draft.py -v`
Expected: FAIL — `DraftOutreach() got an unexpected keyword argument 'critic'`.

- [ ] **Step 3: Implement**

Deterministic validator first, critic second, one shared retry budget — not a second independent one. Persist `critic_verdict`, `critic_concerns`, `critic_overall_note` whatever the outcome. The critic never changes state.

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/application -v`
Expected: PASS (6 new tests, existing drafting tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/application backend/app/adapters backend/app/ports backend/tests
git commit -m "feat: advisory voice critic on drafts, one shared retry"
```

---

## Task 14: Frontend scaffold and API client

**Files:**
- Create: `frontend/package.json`, `vite.config.js`, `index.html`, `src/main.jsx`, `src/App.jsx`, `src/api/client.js`, `src/styles.css`, `src/test/setup.js`, `src/test/server.js`
- Test: `frontend/src/api/client.test.js`

**Interfaces:**
- Produces: `apiGet(path)`, `apiPost(path, body)` throwing `ApiError{status, errors}`; hooks `useStats()`, `useQueue()`, `useJobs()`, `useContacts(filters)`, `useThread(id)`, and mutations `useApprove()`, `useEdit()`, `useReject()`, `useRedraft()`, `useSetEmail()`, `useSend()`, `useStartJob()`, `useSetLabel()`.

- [ ] **Step 1: Scaffold and write the failing test**

```bash
cd frontend && npm create vite@latest . -- --template react && npm i @tanstack/react-query && npm i -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom msw
```

```javascript
// frontend/src/api/client.test.js
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { ApiError, apiGet, apiPost } from './client'

const server = setupServer()
beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('returns parsed JSON on success', async () => {
  server.use(http.get('/api/stats', () => HttpResponse.json({ counts: { new: 2 } })))
  expect(await apiGet('/stats')).toEqual({ counts: { new: 2 } })
})

it('surfaces validator errors from a 422 instead of throwing a generic error', async () => {
  server.use(http.post('/api/outreach/1/edit', () =>
    HttpResponse.json({ errors: ['contains a semicolon'] }, { status: 422 })))
  await expect(apiPost('/outreach/1/edit', {})).rejects.toMatchObject({
    status: 422, errors: ['contains a semicolon'],
  })
})

it('carries the running job id out of a 409', async () => {
  server.use(http.post('/api/jobs/scrape', () =>
    HttpResponse.json({ job_id: 7 }, { status: 409 })))
  await expect(apiPost('/jobs/scrape')).rejects.toMatchObject({ status: 409 })
})

it('never swallows a network failure silently', async () => {
  server.use(http.get('/api/stats', () => HttpResponse.error()))
  await expect(apiGet('/stats')).rejects.toBeInstanceOf(Error)
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/api/client.test.js`
Expected: FAIL — `./client` does not exist.

- [ ] **Step 3: Write the client and query hooks**

`vite.config.js` proxies `/api` to `http://127.0.0.1:8000` in dev and sets the Vitest `jsdom` environment. Query defaults: `retry: 1`, `staleTime: 5000`.

- [ ] **Step 4: Run the test**

Run: `cd frontend && npx vitest run`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: frontend scaffold with typed API client and MSW tests"
```

---

## Task 15: Pipeline screen — funnel and job buttons

**Files:**
- Create: `frontend/src/screens/Pipeline.jsx`, `frontend/src/components/JobButton.jsx`, `frontend/src/components/Banner.jsx`
- Test: `frontend/src/screens/Pipeline.test.jsx`

**Interfaces:**
- Consumes: `useStats`, `useJobs`, `useStartJob`.
- Produces: the `/` route.

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/src/screens/Pipeline.test.jsx
it('shows the funnel counts by state', async () => {
  mockStats({ counts: { new: 249, needs_manual_email: 20, sent: 3 } })
  render(<Pipeline />)
  expect(await screen.findByText('249')).toBeInTheDocument()
})

it('starts a job when its button is clicked', async () => {
  const started = mockStartJob('scrape')
  render(<Pipeline />)
  await userEvent.click(await screen.findByRole('button', { name: /scrape/i }))
  expect(started).toHaveBeenCalled()
})

it('shows live progress while a job runs', async () => {
  mockJobs([{ id: 1, kind: 'scrape', status: 'running', done: 30, total: 145 }])
  render(<Pipeline />)
  expect(await screen.findByText(/30\s*\/\s*145/)).toBeInTheDocument()
})

it('disables a kind that is already running', async () => {
  mockJobs([{ id: 1, kind: 'scrape', status: 'running', done: 1, total: 145 }])
  render(<Pipeline />)
  expect(await screen.findByRole('button', { name: /scrape/i })).toBeDisabled()
})

it('shows a failed job with its error instead of hiding it', async () => {
  mockJobs([{ id: 2, kind: 'resolve', status: 'failed', error: 'VerifierError' }])
  render(<Pipeline />)
  expect(await screen.findByText(/VerifierError/)).toBeInTheDocument()
})

it('stops polling once no job is running', async () => {
  const spy = mockJobs([{ id: 3, kind: 'sync', status: 'succeeded' }])
  render(<Pipeline />)
  await screen.findByText(/sync/i)
  const callsAfterSettle = spy.mock.calls.length
  await new Promise((r) => setTimeout(r, 2500))
  expect(spy.mock.calls.length).toBe(callsAfterSettle)
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/screens/Pipeline.test.jsx`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the screen**

Poll `/api/jobs` every 2s **only while any job is `running`** (`refetchInterval` returns `false` otherwise). A 409 from a start disables the button rather than showing an error.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: pipeline screen with funnel counts and job progress"
```

---

## Task 16: Review Queue screen

**Files:**
- Create: `frontend/src/screens/ReviewQueue.jsx`, `frontend/src/components/DraftPane.jsx`, `frontend/src/components/SourcePane.jsx`, `frontend/src/hooks/useKeyboard.js`
- Test: `frontend/src/screens/ReviewQueue.test.jsx`, `frontend/src/hooks/useKeyboard.test.js`

**Interfaces:**
- Consumes: `useQueue`, `useApprove`, `useEdit`, `useReject`, `useRedraft`.

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/src/screens/ReviewQueue.test.jsx
it('shows the composed email, not the bare stored body', async () => {
  mockQueue([item({ body: 'Body text.', wire_text: 'Hi Akash,\n\nBody text.\n\nSuryansh' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/Hi Akash,/)).toBeInTheDocument()
})

it('shows the cited source beside the draft so a claim is checkable', async () => {
  mockQueue([item({ source_kind: 'news', source_text: 'OpenAI backs Vahan' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/OpenAI backs Vahan/)).toBeInTheDocument()
})

it('approves on "a" and advances to the next draft', async () => {
  const approve = mockApprove()
  mockQueue([item({ outreach_id: 1 }), item({ outreach_id: 2, company_name: 'Second Co' })])
  render(<ReviewQueue />)
  await screen.findByText(/1 of 2/)
  await userEvent.keyboard('a')
  expect(approve).toHaveBeenCalledWith(1)
  expect(await screen.findByText(/Second Co/)).toBeInTheDocument()
})

it('rolls back the optimistic advance when approve fails', async () => {
  mockApproveFailure()
  mockQueue([item({ outreach_id: 1, company_name: 'First Co' })])
  render(<ReviewQueue />)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/First Co/)).toBeInTheDocument()
  expect(await screen.findByText(/could not approve/i)).toBeInTheDocument()
})

it('shows validator errors verbatim when an edit is rejected', async () => {
  mockEditFailure(['contains a semicolon', 'no explicit internship ask'])
  mockQueue([item({})])
  render(<ReviewQueue />)
  await userEvent.keyboard('e')
  await userEvent.click(await screen.findByRole('button', { name: /save/i }))
  expect(await screen.findByText('contains a semicolon')).toBeInTheDocument()
  expect(screen.getByText('no explicit internship ask')).toBeInTheDocument()
})

it('keeps the typed text when an edit is rejected', async () => {
  mockEditFailure(['contains a semicolon'])
  mockQueue([item({})])
  render(<ReviewQueue />)
  await userEvent.keyboard('e')
  const box = await screen.findByRole('textbox', { name: /body/i })
  await userEvent.clear(box)
  await userEvent.type(box, 'my own words')
  await userEvent.click(screen.getByRole('button', { name: /save/i }))
  expect(await screen.findByDisplayValue(/my own words/)).toBeInTheDocument()
})

it('marks a needs-manual-write draft and refuses a bare approve', async () => {
  mockQueue([item({ last_error: 'cited posting is not intern-appropriate' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/needs manual write/i)).toBeInTheDocument()
})

it('shows the critic note as one dismissible line', async () => {
  mockQueue([item({ critic_verdict: 'NEEDS_REVISION', critic_note: 'opener is generic' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/opener is generic/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
  expect(screen.queryByText(/opener is generic/)).not.toBeInTheDocument()
})

it('ignores review keys while the editor is open', async () => {
  const approve = mockApprove()
  mockQueue([item({})])
  render(<ReviewQueue />)
  await userEvent.keyboard('e')
  await userEvent.type(await screen.findByRole('textbox', { name: /body/i }), 'a')
  expect(approve).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/screens/ReviewQueue.test.jsx`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the screen**

Two panes. `useKeyboard` binds `a/e/d/r/s/j/k`, and **unbinds while a text input has focus** — the last test is the one that keeps that honest. Optimistic advance with rollback and a visible error on failure.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: keyboard-driven review queue with optimistic approve"
```

---

## Task 17: Contacts, Thread and manual-email screens

**Files:**
- Create: `frontend/src/screens/Contacts.jsx`, `frontend/src/screens/Thread.jsx`, `frontend/src/screens/ManualEmail.jsx`, `frontend/src/components/DataTable.jsx`
- Test: `frontend/src/screens/Contacts.test.jsx`, `Thread.test.jsx`, `ManualEmail.test.jsx`

- [ ] **Step 1: Write the failing tests**

```javascript
// Contacts.test.jsx
it('lists every company and founder', async () => { /* 2 rows */ })
it('filters by state without hiding non-hiring companies by default', async () => { /* D19 */ })
it('links a founder to their thread', async () => { /* href contains /thread/ */ })

// Thread.test.jsx
it('shows outbound and inbound messages in order', async () => {})
it('shows the label with its rationale', async () => {})
it('corrects a label in one click', async () => {})
it('links out to the gmail thread', async () => {})

// ManualEmail.test.jsx
it('shows the candidates already tried with their verdicts', async () => {})
it('submits a hand-typed address', async () => {})
it('shows the published-email hint when resolve found one', async () => {})
it('marks a founder unresolved on give up', async () => {})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run`
Expected: FAIL — modules missing.

- [ ] **Step 3: Write the three screens**

`DataTable` is a plain table — dense, sortable headers, no card grid.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: contacts, thread and manual-email screens"
```

---

## Task 18: Visual design pass

**Files:**
- Modify: `frontend/src/styles.css`, all screens
- Test: `frontend/src/screens/__snapshots__` (structural assertions only, no pixel snapshots)

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/src/styles.test.js
it('defines the full palette on :root, not only inside a media query', () => {
  const css = readFileSync('src/styles.css', 'utf8')
  const root = css.slice(css.indexOf(':root'), css.indexOf('}', css.indexOf(':root')))
  for (const token of ['--bg', '--fg', '--muted', '--accent', '--border', '--danger']) {
    expect(root).toContain(token)
  }
})

it('keeps a side gutter at phone width', () => {
  expect(readFileSync('src/styles.css', 'utf8')).toMatch(/padding-inline:\s*(1[0-9]|[2-9][0-9])px/)
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/styles.test.js`
Expected: FAIL — tokens absent.

- [ ] **Step 3: Write the stylesheet**

One accent colour, system font stack, 14px base, dense line-height, real tables, `prefers-color-scheme` dark variant defined by re-declaring tokens only. No component library, no gradients, no card grid.

- [ ] **Step 4: Run everything and look at it**

Run: `cd frontend && npx vitest run && npm run dev`
Open `http://localhost:5173` with the API running and read one real draft on the Review screen. Fix what looks wrong before committing.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "style: dense information-first visual pass"
```

---

## Task 19: Scheduled trigger and operator docs

**Files:**
- Create: `scripts/schedule-tasks.ps1`
- Modify: `backend/README.md`, `README.md`
- Test: `backend/tests/api/test_scheduled_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_scheduled_endpoints.py
"""The scheduled task calls the same endpoints the UI buttons call —
one code path per stage (spec A1)."""
def test_poll_replies_is_startable_as_a_job(client, job_probe):
    assert client.post("/api/jobs/poll-replies").status_code == 200


def test_schedule_follow_ups_is_startable_as_a_job(client, job_probe):
    assert client.post("/api/jobs/schedule-follow-ups").status_code == 200


def test_a_scheduled_start_is_recorded_as_created_by_schedule(client, job_probe):
    client.post("/api/jobs/poll-replies", json={"created_by": "schedule"})
    row = next(j for j in client.get("/api/jobs").json() if j["kind"] == "poll-replies")
    assert row["created_by"] == "schedule"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/api/test_scheduled_endpoints.py -v`
Expected: FAIL — kinds not registered.

- [ ] **Step 3: Register the kinds and write the script**

```powershell
# scripts/schedule-tasks.ps1 — run once, as the user
$action = New-ScheduledTaskAction -Execute "curl.exe" `
  -Argument '-s -X POST http://127.0.0.1:8000/api/jobs/poll-replies -H "Content-Type: application/json" -d "{\"created_by\":\"schedule\"}"'
Register-ScheduledTask -TaskName "YCAutoApply-PollReplies" -Trigger (New-ScheduledTaskTrigger -Daily -At 9am) -Action $action
```

- [ ] **Step 4: Update the READMEs**

Root `README.md` gains a Phase 2 quickstart: `docker compose up -d`, `alembic upgrade head`, `uvicorn app.api.main:app --host 127.0.0.1 --port 8000`, `npm run dev`, re-run `gmail-auth` once for the read scope, then `scripts/schedule-tasks.ps1`. State plainly that the API has no auth and must stay on localhost.

- [ ] **Step 5: Run the tests and commit**

```bash
git add backend scripts README.md
git commit -m "feat: scheduled poll and follow-up triggers with operator docs"
```

---

## Task 20: End-to-end and the whole-suite gate

**Files:**
- Create: `backend/tests/test_e2e_phase2.py`
- Test: the whole suite, both sides

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_e2e_phase2.py
"""One offline pass through the Phase 2 loop, on the fakes:
draft -> approve over HTTP -> send -> reply arrives -> classified ->
follow-up cancelled."""
def test_a_reply_cancels_the_follow_up_sequence(client, repos, fake_reader, fake_classifier,
                                                fake_sender, clock, ready_founder, fake_drafter):
    DraftOutreach(fake_drafter, _draft_repos(repos)).run(limit=1)
    outreach_id = ready_founder.outreach.id
    assert client.post(f"/api/outreach/{outreach_id}/approve").status_code == 200
    assert client.post("/api/send", json={"limit": 1}).json()["sent"] == 1

    thread_id = repos.message.get_outbound(outreach_id, 0).gmail_thread_id
    fake_reader.add(thread_id=thread_id, message_id="r1",
                    sender="founder@example.com", body="Sure, let's talk Friday.")
    PollReplies(fake_reader, _poll_repos(repos), clock).run()
    fake_classifier.next = {"label": "INTERESTED", "confidence": 0.9, "rationale": "wants to talk"}
    ClassifyReply(fake_classifier, _classify_repos(repos), clock).run()

    outreach = repos.outreach.get(outreach_id)
    assert outreach.state.value == "replied"
    assert outreach.next_action_at is None
    assert ScheduleFollowUps(_follow_repos(repos), clock).run().scheduled == 0

    thread = client.get(f"/api/outreach/{outreach_id}/thread").json()
    assert [m["direction"] for m in thread["messages"]] == ["outbound", "inbound"]
    assert thread["messages"][1]["label"] == "INTERESTED"


def test_no_reply_produces_a_follow_up_in_the_approval_queue(client, repos, fake_sender, clock,
                                                            sent_five_days_ago, fake_drafter):
    ScheduleFollowUps(_follow_repos(repos), clock).run()
    DraftOutreach(fake_drafter, _draft_repos(repos)).run(limit=1)
    item = client.get("/api/queue").json()[0]
    assert item["sequence_step"] == 1
    # Still gated on a human.
    assert repos.message.get_outbound(sent_five_days_ago.outreach.id, 1).sent_at is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/test_e2e_phase2.py -v`
Expected: FAIL until every prior task is in place.

- [ ] **Step 3: Make it pass**

Fill only the gaps the test exposes. Any behaviour change here means an earlier task's tests were too weak — add the test there too.

- [ ] **Step 4: The full gate**

Run:
```bash
cd backend && env -i PATH="$PATH" python -m pytest -q
cd ../frontend && npx vitest run
```
Expected: backend green with no env vars and no network; frontend green.

- [ ] **Step 5: Live smoke (operator, not the agent)**

With Postgres up, the API running and the frontend open: start `sync` from the Pipeline screen, watch progress, review one draft with the keyboard, and confirm the Thread screen renders. Record anything surprising in the report rather than fixing it silently.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_e2e_phase2.py
git commit -m "test: end-to-end reply and follow-up loop offline"
```

---

## Self-Review

**Spec coverage.** §2 amendments → Tasks 6 (jobs replace workers), 7 (read scope), 19 (scheduled trigger), 11 (R-C lifted). §3 architecture → Tasks 3–6. §4 jobs → Tasks 1, 2, 6. §5 replies → Tasks 7, 8, 9, 10. §6 follow-ups → Tasks 11, 12. §7 API → Tasks 3, 4, 5, 6, 10. §8 frontend → Tasks 14–18. §9 error handling → Tasks 2 (type-name-only), 6 (failed jobs), 9 (unclassified stays visible). §10 testing → every task, gated in Task 20. D22 critic → Task 13. §11 out-of-scope items appear in no task, as intended.

**Placeholder scan.** No TBDs. Tasks 17 and 18 give test names with empty bodies rather than full code — deliberate, as they are the most mechanical screens and the assertions are named precisely; every other task carries runnable test code.

**Type consistency.** `RunJob.start/progress/finish/fail/recover_stale` used identically in Tasks 2 and 6. `JOB_KINDS` keys match the `POST /api/jobs/{kind}` paths in Tasks 6 and 19. `PollResult`, `ClassifyResult`, `FollowUpResult` field names match between their defining tasks and Task 20. `QueueItemOut` field names in Task 4 match the frontend's `item({...})` fixtures in Task 16. `LABELS` in Task 9 matches the correction endpoint's validation in Task 10.

**Known risk.** Task 6's threading is the least conventional part: a background thread with its own Session inside a FastAPI process. The tests pin the behaviour that matters (own session, 409, progress, failure recovery), but this is the task to review hardest.
