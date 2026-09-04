"""SQLAlchemy repositories over the models in `app.adapters.db.models`.

Every repo exposes `upsert`, `get` and `list_by_state`, per Task 9's
Interfaces block. The brief pins the method NAMES but not their exact
signatures (unlike Task 8's ports, which ship a test file that fixes every
Protocol method to the byte), so each `list_by_state` below is implemented
against the closest thing that table actually has to a workflow state:

- `OutreachRepo.list_by_state` filters `Outreach.state` directly — this is
  the literal, central case: every stage worker (spec §5/§8) claims the
  `outreach` rows due for its own stage by state.
- `CompanyRepo.list_by_state` filters `Company.status` (e.g. "Active") —
  companies have no separate `state` column, so `status` is the closest
  analogue.
- `FounderRepo.list_by_state` and `MessageRepo.list_by_state` join through
  to `Outreach.state`, since founders and messages don't own a state
  column themselves but each belongs to exactly one `Outreach` row that
  does (one outreach per founder; every message belongs to an outreach).
  This mirrors real call sites in the spec — e.g. the Review Queue (§16)
  lists messages for outreach rows in `PENDING_APPROVAL`.
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

    def list_by_state(self, status: str) -> list[Company]:
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
        return list(
            self.session.execute(
                select(Outreach).where(Outreach.state == str(state))
            ).scalars()
        )


class MessageRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, message: Message) -> Message:
        """Merge by primary key `id` (insert if unset, else update).

        Messages have no natural key beyond `id` — the reserve-before-send
        double-send guard (spec §13) is enforced by the database's unique
        constraint on a plain INSERT, not by upsert-by-natural-key
        semantics, so callers that need the "reserve" step should add a
        `Message` to the session directly (as the Task 9 constraint tests
        do) rather than go through `upsert`.
        """
        merged = self.session.merge(message)
        self.session.flush()
        return merged

    def get(self, id: int) -> Message | None:
        return self.session.get(Message, id)

    def list_by_state(self, state: OutreachState | str) -> list[Message]:
        return list(
            self.session.execute(
                select(Message)
                .join(Outreach, Outreach.id == Message.outreach_id)
                .where(Outreach.state == str(state))
            ).scalars()
        )
