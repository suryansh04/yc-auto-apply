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
since ruling R1 the draft-time outbound row is the reservation and the
guard is `MessageRepo.reserve_for_send` - a single conditional `UPDATE`
whose rowcount says whether the caller won - backed by the partial unique
index on outbound `(outreach_id, sequence_step)` and the
`sent_requires_approved` CHECK. The constraint tests in
`tests/adapters/test_repositories.py` add `Message` rows to the session
directly rather than going through this repo.

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

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.adapters.db.models import (
    AppSettings,
    Company,
    CompanyLaunch,
    CompanyNews,
    EmailCandidate,
    Founder,
    GmailCredential,
    Job,
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

    def get_by_thread_id(self, thread_id: str) -> Outreach | None:
        """Added in Task 8 (spec §5): `PollReplies` maps an inbound Gmail
        message's `threadId` back to the outreach it belongs to by joining
        through `Message.gmail_thread_id` — every outbound step we send on
        an outreach shares the same Gmail thread, so this resolves to
        exactly one outreach even when a follow-up step has already gone
        out. A `threadId` with no matching message (the operator's own
        ordinary inbox mail) returns `None` — the caller's cue to skip it,
        never storing or classifying it."""
        return self.session.execute(
            select(Outreach)
            .join(Message, Message.outreach_id == Outreach.id)
            .where(Message.gmail_thread_id == thread_id)
            .limit(1)
        ).scalars().first()

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

    def all(self) -> list[Outreach]:
        """Task 5: every outreach row, unfiltered - the contacts list's
        base query (`GET /api/contacts` applies its own filters after this,
        the same shape as every `list_by_state`/`list_by_status` above:
        no ordering, no limit)."""
        return list(self.session.execute(select(Outreach)).scalars())

    def list_due(self) -> list[Outreach]:
        """Task 11 (spec §6), narrowed in review fix round 2 (ruling
        T11-3): every `SENT` row, full stop. Filters only on `state` —
        `sequence_step` and `next_action_at` are both left to the
        application layer.

        Fix round 1 shipped this filtered on `next_action_at IS NOT NULL
        AND next_action_at <= now`, which made `ScheduleFollowUps`'s live
        settings re-check (spec §7: an operator's interval change applies
        to pending rows) work in ONE direction only: LENGTHENING an
        interval correctly deferred a row (the app-layer recompute says
        not-due), but SHORTENING one could never accelerate a row ahead of
        its stale stored `next_action_at`, because this SQL excluded the
        row before the recompute ever ran. That defeated the whole point
        of re-deriving due-ness live. Dropping the time filter here fixes
        it: `ScheduleFollowUps` now sees every `SENT` row and decides
        due-ness itself, entirely from the last outbound message's
        `sent_at` plus the live `settings.followup_day_1`/`followup_day_2`
        (never from this row's own `next_action_at`, which is written by
        `SendApproved` purely for display/audit purposes now).

        A `sequence_step < 2` filter was considered and rejected: a
        `sequence_step == 2` row must still come back here so
        `ScheduleFollowUps` can close it out (spec §6, "after step 2 with
        no human reply, the row moves to CLOSED") — filtering it out at
        the SQL layer would make closing unreachable.

        At the pipeline's real scale (hundreds of outreach rows, spec's
        own count) an unfiltered `state = SENT` scan costs nothing
        measurable; there is no locking or limit here for the same reason
        `list_by_state` has none (no concurrent worker contention yet in
        Phase 2's single job runner)."""
        return list(
            self.session.execute(
                select(Outreach).where(Outreach.state == str(OutreachState.SENT))
            ).scalars()
        )


class MessageRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> Message:
        """Insert or update, keyed on `id` (Message has no other natural
        key). Update when `fields["id"]` is given and matches an existing
        row; insert a new row otherwise.

        This is NOT the reserve-before-send double-send guard (spec §13).
        Since ruling R1 the draft-time outbound row is the reservation and
        the guard is `reserve_for_send` below — a single conditional
        UPDATE whose rowcount says whether this caller won. `upsert`'s
        update-if-present semantics would silently paper over a lost race,
        so `SendApproved` never uses it to claim a row, only to record the
        Gmail ids afterwards.
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

    def get_by_gmail_id(self, gmail_message_id: str) -> Message | None:
        """Added in Task 8 (spec §5): the dedupe check `PollReplies` runs
        BEFORE calling `GmailReader.get_message` for a given id — backed by
        `uq_messages_gmail_message_id` (Task 1), so re-polling the same
        window costs no network call, not just no duplicate row."""
        return self.session.execute(
            select(Message).where(Message.gmail_message_id == gmail_message_id)
        ).scalar_one_or_none()

    def list_by_outreach(self, outreach_id: int) -> list[Message]:
        """Added in Task 8: the brief's own tests call it by this name.
        Equivalent to filtering `list_by_state`/similar by outreach id —
        every message (outbound and inbound) belonging to one outreach."""
        return list(
            self.session.execute(
                select(Message).where(Message.outreach_id == outreach_id)
            ).scalars()
        )

    def list_unclassified_inbound(self) -> list[Message]:
        """Added in Task 9 (spec §5): what `ClassifyReply` classifies each
        run — every inbound reply not yet labelled. An already-labelled row
        (a prior run, or an operator's manual correction via Task 10) is
        never returned here, so `ClassifyReply` never even calls the
        classifier for it, let alone overwrites it."""
        return list(
            self.session.execute(
                select(Message).where(Message.direction == "inbound", Message.label.is_(None))
            ).scalars()
        )

    def get_outbound(self, outreach_id: int, sequence_step: int) -> Message | None:
        """Added in fix round 1 of Tasks 13/14 (F1, ruling R1): the
        draft-time outbound row for `(outreach_id, sequence_step)` IS the
        send reservation guarded by `uq_messages_outreach_step_outbound`.
        `DraftOutreach` looks the row up by this natural key and updates it
        in place on a redraft (`PENDING_APPROVAL -> READY_TO_DRAFT`) instead
        of inserting a second row the index would reject."""
        return self.session.execute(
            select(Message).where(
                Message.outreach_id == outreach_id,
                Message.sequence_step == sequence_step,
                Message.direction == "outbound",
            )
        ).scalar_one_or_none()

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

    def reserve_for_send(self, message_id: int, now: datetime) -> int:
        """Task 15 (spec §13, ruling R1): the reserve step. ONE conditional
        statement — never read-then-write —

            UPDATE messages SET sent_at = :now
             WHERE id = :id AND sent_at IS NULL AND approved_at IS NOT NULL

        and its rowcount is the verdict: 1 means this caller holds the
        reservation and may call Gmail; 0 means the row is already
        reserved/sent, or was never approved, or does not exist — never
        send. Two callers racing on the same row cannot both see 1,
        because the second UPDATE evaluates its WHERE against the first's
        committed `sent_at`. `synchronize_session="fetch"` keeps any
        already-loaded `Message` instance's `sent_at` in step with the
        row. The caller commits immediately after."""
        result = self.session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.sent_at.is_(None),
                Message.approved_at.is_not(None),
            )
            .values(sent_at=now)
            .execution_options(synchronize_session="fetch")
        )
        return int(result.rowcount)

    def count_sent_since(self, since: datetime) -> int:
        """Task 15: outbound messages with `sent_at >= since`. `SendApproved`
        subtracts this (with `since` = now minus 24 hours, ruling R-A's
        rolling window) from `daily_send_cap`, so re-running the CLI never
        exceeds the cap. Reserved-but-unrecorded
        rows count too — deliberately conservative."""
        return int(
            self.session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.direction == "outbound", Message.sent_at >= since)
            ).scalar_one()
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
        application-side only, same as every other repo's `upsert`.

        Final fix wave (F14): when the payload carries no `url`, the key
        falls back to `(company_id, title)`. `JobPosting.url == None`
        compiles to `url IS NULL`, so every url-less posting at a company
        used to collapse into the first one's row - silent data loss."""
        url = fields.get("url")
        if url is not None:
            key = (JobPosting.url == url,)
        else:
            key = (JobPosting.url.is_(None), JobPosting.title == fields.get("title"))
        existing = self.session.execute(
            select(JobPosting).where(JobPosting.company_id == fields["company_id"], *key)
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
        """Insert or update. Keyed on `id` when given (an unknown `id`
        inserts a row with that id — it never falls back to rewriting the
        current row's primary key, fix round 1 F12); with no `id`, updates
        the existing (single) row if there is one, else inserts it."""
        existing = None
        if fields.get("id") is not None:
            existing = self.session.get(ResumeProfile, fields["id"])
        else:
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


# --- Added in Task 15 (spec §14): the Gmail OAuth refresh token, one row per
# sending account, written by `gmail-auth` and read by `send`. Same
# `upsert(**fields)` / `get(id)` shape as every repo above; `upsert` is keyed
# on `account_email` (the table's own unique constraint), so re-running
# `gmail-auth` for the same account replaces its refresh token in place.


class GmailCredentialRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, **fields) -> GmailCredential:
        """Insert or update, keyed on the natural key `account_email`."""
        existing = self.session.execute(
            select(GmailCredential).where(
                GmailCredential.account_email == fields["account_email"]
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = GmailCredential(**fields)
            self.session.add(existing)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def get(self, id: int) -> GmailCredential | None:
        return self.session.get(GmailCredential, id)

    def get_by_account_email(self, account_email: str) -> GmailCredential | None:
        return self.session.execute(
            select(GmailCredential).where(GmailCredential.account_email == account_email)
        ).scalar_one_or_none()

    def set_history_id(self, account_email: str, history_id: str) -> None:
        """Task 7 (spec §5): the narrow write the reply poller (Task 8)
        needs after a successful `GmailReader.history_since` or
        `current_history_id()` call - update just the stored cursor
        without touching the refresh token or scopes. Thin wrapper over
        `upsert`, which already updates only the fields it is given;
        deciding *when* to call this is Task 8's job, not this repo's or
        `GmailReader`'s."""
        self.upsert(account_email=account_email, last_history_id=history_id)


# --- Added in Task 2 (Phase 2, spec §4): bookkeeping for a background stage
# run, one row per invocation. Same `upsert(**fields)` shape as every repo
# above, keyed on `id` (like `MessageRepo`) since a job has no natural key
# beyond it; `RunJob` (app.application.run_job) is the only caller.


class JobRepo:
    """Phase 2 (spec §4). Uniform `upsert(**fields)` like every other repo."""

    def __init__(self, session: Session):
        self._session = session

    def upsert(self, **fields) -> Job:
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

    def get(self, job_id: int) -> Job | None:
        return self._session.get(Job, job_id)

    def active(self, kind: str) -> Job | None:
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


# --- Added in Task 10 (spec §7, T9-1): the one-row `settings` table -
# `followup_day_1`/`followup_day_2`, the source `ClassifyReply.apply_label`'s
# restore path (a human label hand-corrected back to a machine one) reads
# the follow-up interval from, falling back to `SendApproved`'s own
# defaults (5/12 days) when there is no row yet.


class SettingsRepo:
    def __init__(self, session: Session):
        self.session = session

    def get(self) -> AppSettings | None:
        """The single `settings` row (spec §7 describes it as a singleton
        operator-configured table), or `None` before an operator has ever
        saved one - callers fall back to their own defaults rather than
        this repo inventing one."""
        return self.session.execute(select(AppSettings)).scalars().first()
