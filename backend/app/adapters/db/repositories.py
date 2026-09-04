"""SQLAlchemy repositories over the models in `app.adapters.db.models`.

Every repo exposes `upsert` and `get`; `FounderRepo`, `OutreachRepo` and
`MessageRepo` also expose `list_by_state` (per Task 9's Interfaces block).
`CompanyRepo` exposes `list_by_status` instead — see below for why that one
name deliberately differs.

The brief pins the method NAMES but not their exact signatures (unlike
Task 8's ports, which ship a test file that fixes every Protocol method to
the byte), so each listing method below is implemented against the closest
thing that table actually has to a workflow state:

- `OutreachRepo.list_by_state` filters `Outreach.state` directly — the
  literal, central case.
- `FounderRepo.list_by_state` and `MessageRepo.list_by_state` join through
  to `Outreach.state`, since founders and messages don't own a state
  column themselves but each relates to exactly one `Outreach` row that
  does (one outreach per founder; every message belongs to an outreach).
  This mirrors real call sites in the spec — e.g. the Review Queue (§16)
  lists messages for outreach rows in `PENDING_APPROVAL`.
- `CompanyRepo.list_by_status` filters `Company.status` (e.g. "Active") —
  this is YC's own lifecycle status for the company, a genuinely different
  concept from the outreach pipeline's `OutreachState`, so it is named
  `list_by_status` rather than overloaded onto `list_by_state`. All three
  `list_by_state` methods share the keyword `state=`; `list_by_status`
  uses `status=`.

`upsert` is uniform across all four repos: it always accepts the row's
fields as `**fields` and is keyed on that table's natural key — `yc_id`
(`Company`), `yc_user_id` (`Founder`), `founder_id` (`Outreach`, one
outreach per founder). `Message` has no natural key beyond `id`, so
`MessageRepo.upsert` is keyed on `id` when given (update), or inserts a new
row otherwise. Note that `MessageRepo.upsert` is NOT the double-send guard:
the reserve-before-send protocol (spec §13) relies on a plain `INSERT`
failing the database's unique constraint, which is why the constraint
tests in `tests/adapters/test_repositories.py` add `Message` rows to the
session directly rather than going through this repo.

IMPORTANT — what `list_by_state` is NOT: it is a plain, unordered
`SELECT ... WHERE state = :state`, with no `next_action_at` filter, no
row locking and no limit. That is genuinely sufficient for Phase 1, which
drives every stage from a single-process CLI with no concurrent workers —
there is no contention to guard against yet. Spec §5/§8's stage-worker
claim query (`next_action_at <= now()`, `FOR UPDATE SKIP LOCKED`, a limit)
is a *different*, not-yet-built primitive for Phase 2's concurrent
workers — it belongs in a dedicated method (e.g. `claim_due(state, now,
limit)`) when that phase actually needs it, not bolted onto this one.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.db.models import Company, Founder, Message, Outreach
from app.domain.states import OutreachState


class CompanyRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> Company:
        """Insert or update, keyed on the natural key `yc_id`."""
        existing = self.session.execute(
            select(Company).where(Company.yc_id == fields["yc_id"])
        ).scalar_one_or_none()
        if existing is None:
            existing = Company(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> Company | None:
        return self.session.get(Company, id)

    def list_by_status(self, status: str) -> list[Company]:
        return list(
            self.session.execute(
                select(Company).where(Company.status == status)
            ).scalars()
        )


class FounderRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> Founder:
        """Insert or update, keyed on the natural key `yc_user_id`."""
        existing = self.session.execute(
            select(Founder).where(Founder.yc_user_id == fields["yc_user_id"])
        ).scalar_one_or_none()
        if existing is None:
            existing = Founder(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> Founder | None:
        return self.session.get(Founder, id)

    def list_by_state(self, state: OutreachState | str) -> list[Founder]:
        """Plain unordered listing (see module docstring) of founders whose
        `Outreach` row is currently in `state`. Not a claim primitive."""
        return list(
            self.session.execute(
                select(Founder)
                .join(Outreach, Outreach.founder_id == Founder.id)
                .where(Outreach.state == str(state))
            ).scalars()
        )


class OutreachRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> Outreach:
        """Insert or update, keyed on the natural key `founder_id` (one
        outreach row per founder)."""
        existing = self.session.execute(
            select(Outreach).where(Outreach.founder_id == fields["founder_id"])
        ).scalar_one_or_none()
        if existing is None:
            existing = Outreach(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> Outreach | None:
        return self.session.get(Outreach, id)

    def list_by_state(self, state: OutreachState | str) -> list[Outreach]:
        """Plain unordered `WHERE state = :state` listing — sufficient for
        Phase 1's single-process CLI, where nothing else is contending for
        these rows. This is deliberately NOT the spec §5/§8 stage-worker
        claim query: it has no `next_action_at` filter, no `FOR UPDATE
        SKIP LOCKED`, and no limit. Phase 2's concurrent workers will need
        a separate `claim_due(state, now, limit)` for that; do not repurpose
        this method for it."""
        return list(
            self.session.execute(
                select(Outreach).where(Outreach.state == str(state))
            ).scalars()
        )


class MessageRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> Message:
        """Insert or update, keyed on `id` (Message has no other natural
        key). Update when `fields["id"]` is given and matches an existing
        row; insert a new row otherwise.

        This is NOT the reserve-before-send double-send guard (spec §13):
        that relies on a plain `INSERT` failing the database's unique
        constraint on first write, which `upsert`'s update-if-present
        semantics would silently paper over. Callers implementing the
        "reserve" step should add a `Message` to the session directly (as
        `tests/adapters/test_repositories.py` does) rather than call this.
        """
        existing = None
        if fields.get("id") is not None:
            existing = self.session.get(Message, fields["id"])
        if existing is None:
            existing = Message(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> Message | None:
        return self.session.get(Message, id)

    def list_by_state(self, state: OutreachState | str) -> list[Message]:
        """Plain unordered listing (see module docstring) of messages whose
        parent `Outreach` is currently in `state` — e.g. the Review Queue
        (spec §16) listing messages for `PENDING_APPROVAL` outreach."""
        return list(
            self.session.execute(
                select(Message)
                .join(Outreach, Outreach.id == Message.outreach_id)
                .where(Outreach.state == str(state))
            ).scalars()
        )
