"""SQLAlchemy repositories over the models in `app.adapters.db.models`.

Every repo exposes `upsert` and `get`; `FounderRepo`, `OutreachRepo` and
`MessageRepo` also expose `list_by_state` (per Task 9's Interfaces block).
`CompanyRepo` exposes `list_by_status` instead — see below for why that one
name deliberately differs. `FounderRepo.get_by_yc_user_id` and
`OutreachRepo.get_by_founder_id` were added in Task 11 for
`ScrapeCompanyPage`, which needs to look a row up by natural key *before*
deciding whether to `upsert` it (see their docstrings). `JobPostingRepo`,
`CompanyNewsRepo` and `CompanyLaunchRepo`, also added in Task 11, follow the
same `upsert`/`get` shape as the four Task 9 repos above.

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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.db.models import (
    Company,
    CompanyLaunch,
    CompanyNews,
    EmailCandidate,
    Founder,
    JobPosting,
    Message,
    Outreach,
    ResumeProfile,
)
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

    def get_by_yc_user_id(self, yc_user_id: int) -> Founder | None:
        """Added in Task 11: lets `ScrapeCompanyPage` tell a brand-new
        founder from a re-scraped one *before* calling `upsert` (whose
        update-if-present semantics erase that distinction), so it creates
        an `Outreach` row only for founders that don't have one yet."""
        return self.session.execute(
            select(Founder).where(Founder.yc_user_id == yc_user_id)
        ).scalar_one_or_none()

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

    def list_by_company_id(self, company_id: int) -> list[Founder]:
        """Added in Task 12: `ResolveEmails` needs every founder at a
        company to decide whether a launch's `published_email` (D18) is
        attributable — it is only auto-assigned when the company has
        exactly one founder (spec §9)."""
        return list(
            self.session.execute(
                select(Founder).where(Founder.company_id == company_id)
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

    def get_by_founder_id(self, founder_id: int) -> Outreach | None:
        """Added in Task 11, the read-side counterpart of `upsert`'s
        `founder_id` key: lets `ScrapeCompanyPage` check whether a founder
        already has an `Outreach` row before creating one in `NEW`, so a
        re-scrape never resets an outreach that has already progressed."""
        return self.session.execute(
            select(Outreach).where(Outreach.founder_id == founder_id)
        ).scalar_one_or_none()

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


# --- Added in Task 11 (spec §3/§9): repos for the research tables a company
# page scrape populates. None of these were needed until `ScrapeCompanyPage`
# existed, so Task 9 never built them. Same `upsert(**fields)` / `get(id)`
# shape as the four repos above; each `upsert` is keyed on the closest thing
# its table has to a natural key, matching how `CompanyRepo`/`FounderRepo`/
# `OutreachRepo` are keyed on theirs.


class JobPostingRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> JobPosting:
        """Insert or update, keyed on `(company_id, url)` — `JobPosting` has
        no DB-level uniqueness (unlike `CompanyNews`), so this dedup is
        application-side only, same as every other repo's `upsert`."""
        existing = self.session.execute(
            select(JobPosting).where(
                JobPosting.company_id == fields["company_id"],
                JobPosting.url == fields.get("url"),
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = JobPosting(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> JobPosting | None:
        return self.session.get(JobPosting, id)

    def list_by_company_id(self, company_id: int) -> list[JobPosting]:
        """Added in Task 14: `DraftOutreach` filters a company's postings
        through `is_intern_appropriate` (D23) before offering any as a
        source."""
        return list(
            self.session.execute(
                select(JobPosting).where(JobPosting.company_id == company_id)
            ).scalars()
        )


class CompanyNewsRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> CompanyNews:
        """Insert or update, keyed on `(company_id, url)` — matches the
        table's own `UniqueConstraint("company_id", "url")`."""
        existing = self.session.execute(
            select(CompanyNews).where(
                CompanyNews.company_id == fields["company_id"],
                CompanyNews.url == fields.get("url"),
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = CompanyNews(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> CompanyNews | None:
        return self.session.get(CompanyNews, id)

    def list_by_company_id(self, company_id: int) -> list[CompanyNews]:
        """Added in Task 14: news is the first-priority research source
        (D17) `DraftOutreach` offers the drafter."""
        return list(
            self.session.execute(
                select(CompanyNews).where(CompanyNews.company_id == company_id)
            ).scalars()
        )


class CompanyLaunchRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> CompanyLaunch:
        """Insert or update, keyed on `(company_id, title)` — the parsed
        launch payload (`app.adapters.yc_page.parse_company_page`) carries
        no external id for a launch, so title is the closest available
        natural key."""
        existing = self.session.execute(
            select(CompanyLaunch).where(
                CompanyLaunch.company_id == fields["company_id"],
                CompanyLaunch.title == fields.get("title"),
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = CompanyLaunch(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> CompanyLaunch | None:
        return self.session.get(CompanyLaunch, id)

    def list_by_company_id(self, company_id: int) -> list[CompanyLaunch]:
        """Added in Task 12: `ResolveEmails` reads a company's launches to
        find a `published_email` (D18) short-circuit before spending any
        verification budget."""
        return list(
            self.session.execute(
                select(CompanyLaunch).where(CompanyLaunch.company_id == company_id)
            ).scalars()
        )


# --- Added in Task 12 (spec §9, D30): repo for the email verification
# verdicts `ResolveEmails` persists. Same `upsert`/`get` shape as every repo
# above; `upsert` is keyed on `(founder_id, address)` — the table's own
# `UniqueConstraint` — so re-verifying the same candidate updates its verdict
# in place instead of accumulating duplicate rows.


class EmailCandidateRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> EmailCandidate:
        """Insert or update, keyed on `(founder_id, address)`."""
        existing = self.session.execute(
            select(EmailCandidate).where(
                EmailCandidate.founder_id == fields["founder_id"],
                EmailCandidate.address == fields.get("address"),
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = EmailCandidate(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> EmailCandidate | None:
        return self.session.get(EmailCandidate, id)

    def list_by_founder_id(self, founder_id: int) -> list[EmailCandidate]:
        """Lets `ResolveEmails` skip re-verifying an address a prior run
        already judged (spec §9): a re-run never re-verifies an address
        already judged, so this is queried before spending any candidate's
        verification budget."""
        return list(
            self.session.execute(
                select(EmailCandidate).where(EmailCandidate.founder_id == founder_id)
            ).scalars()
        )


# --- Added in Task 13 (spec §7/§10): the single-row resume profile that
# `ParseResume` writes and `DraftOutreach` reads. Same `upsert(**fields)` /
# `get(id)` shape as every repo above. The table is single-row by design
# (spec §7: "resume_profile — single row"), so `upsert` is keyed on `id`
# when given and otherwise on "the one row that exists", and `save` /
# `get_current` are the single-row conveniences the use cases actually call.


class ResumeProfileRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> ResumeProfile:
        """Insert or update. Keyed on `id` when given; otherwise updates the
        existing (single) row if there is one, else inserts it."""
        existing = None
        if fields.get("id") is not None:
            existing = self.session.get(ResumeProfile, fields["id"])
        if existing is None:
            existing = self.get_current()
        if existing is None:
            existing = ResumeProfile(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> ResumeProfile | None:
        return self.session.get(ResumeProfile, id)

    def get_current(self) -> ResumeProfile | None:
        """The one profile row, or `None` before any resume was parsed."""
        return self.session.execute(
            select(ResumeProfile).order_by(ResumeProfile.id.desc()).limit(1)
        ).scalar_one_or_none()

    def save(self, raw_text: str, claims: list[dict]) -> ResumeProfile:
        """What `ParseResume` calls (its brief's own fake repo has exactly
        this `save(raw_text, claims)` signature): replaces the single
        profile row's text and claims and stamps `parsed_at`."""
        return self.upsert(raw_text=raw_text, claims=claims, parsed_at=func.now())
