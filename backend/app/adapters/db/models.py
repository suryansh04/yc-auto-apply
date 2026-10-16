"""SQLAlchemy 2.0 ORM models for the persistence schema (spec §7).

Scope note: Phase 2 (migration 0003) added the `jobs` table, the voice
critic columns on `Message` (`critic_verdict` / `critic_concerns` /
`critic_overall_note`, D22), the reply classification columns on
`Message` (`label` / `confidence` / `rationale` / `classified_at`,
spec §5), and `GmailCredential.last_history_id` (the Gmail history-API
cursor for reply polling). The `outcomes` table is still not modelled
here.

Two constraints on `Message` are load-bearing (spec §13, D5) and are the
actual point of this module:

- a UNIQUE constraint on (outreach_id, sequence_step) over *outbound*
  messages only, implemented as a partial unique index — this is what
  makes a double-send structurally impossible, even across a crash and
  retry (the "reserve" step of the reserve/send/record protocol);
- a CHECK constraint that `sent_at IS NOT NULL` implies
  `approved_at IS NOT NULL` — this is what makes it impossible for any
  code path to mark a message sent without a human having approved it
  first, independent of whatever the application layer does or forgets.
"""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    MetaData,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Standard Alembic-recommended naming convention. Applied now, before any
# database has ever run this migration, so every constraint gets a
# deterministic name instead of Postgres's implicit ones — the alternative
# is a future `alembic revision --autogenerate` producing spurious
# rename-only diffs the first time this convention gets added.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    yc_id: Mapped[int] = mapped_column(unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255))
    batch: Mapped[str | None] = mapped_column(String(50))
    team_size: Mapped[int | None]
    regions: Mapped[list | None] = mapped_column(JSONB)
    all_locations: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str | None] = mapped_column(String(50))
    stage: Mapped[str | None] = mapped_column(String(50))
    is_hiring: Mapped[bool] = mapped_column(default=False)
    one_liner: Mapped[str | None] = mapped_column(Text)
    long_description: Mapped[str | None] = mapped_column(Text)
    # 2026-09-25: what a web search found about the company, cached (0008):
    # a list of {title, url, content, published_date}.
    web_research: Mapped[list | None] = mapped_column(JSONB)
    web_researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job_postings: Mapped[list["JobPosting"]] = relationship(back_populates="company")
    founders: Mapped[list["Founder"]] = relationship(back_populates="company")
    news: Mapped[list["CompanyNews"]] = relationship(back_populates="company")
    launches: Mapped[list["CompanyLaunch"]] = relationship(back_populates="company")


class JobPosting(Base):
    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1000))
    employment_type: Mapped[str | None] = mapped_column(String(50))
    role_specific_type: Mapped[str | None] = mapped_column(String(100))
    salary_range: Mapped[str | None] = mapped_column(String(100))
    min_experience: Mapped[str | None] = mapped_column(String(100))
    skills: Mapped[list | None] = mapped_column(JSONB)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    company: Mapped["Company"] = relationship(back_populates="job_postings")


class Founder(Base):
    __tablename__ = "founders"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    yc_user_id: Mapped[int] = mapped_column(unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    twitter_url: Mapped[str | None] = mapped_column(String(500))
    bio: Mapped[str | None] = mapped_column(Text)
    # 2026-09-25: what a web search found about them, cached (0007).
    web_research: Mapped[str | None] = mapped_column(Text)
    web_researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped["Company"] = relationship(back_populates="founders")
    email_candidates: Mapped[list["EmailCandidate"]] = relationship(back_populates="founder")
    outreach: Mapped["Outreach"] = relationship(back_populates="founder", uselist=False)


class CompanyNews(Base):
    __tablename__ = "company_news"
    __table_args__ = (UniqueConstraint("company_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    source: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)

    company: Mapped["Company"] = relationship(back_populates="news")


class CompanyLaunch(Base):
    __tablename__ = "company_launches"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    published_email: Mapped[str | None] = mapped_column(String(255))
    raw: Mapped[dict | None] = mapped_column(JSONB)

    company: Mapped["Company"] = relationship(back_populates="launches")


class EmailCandidate(Base):
    __tablename__ = "email_candidates"
    __table_args__ = (UniqueConstraint("founder_id", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    founder_id: Mapped[int] = mapped_column(ForeignKey("founders.id"), index=True)
    address: Mapped[str] = mapped_column(String(320))
    pattern: Mapped[str | None] = mapped_column(String(50))
    priority: Mapped[int | None]
    verdict: Mapped[str | None] = mapped_column(String(20))
    # valid | invalid | catch_all | unknown | published
    provider: Mapped[str | None] = mapped_column(String(50))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    founder: Mapped["Founder"] = relationship(back_populates="email_candidates")


class Outreach(Base):
    __tablename__ = "outreach"
    __table_args__ = (
        # Spec §7/§8: every stage worker claims `outreach` rows by state
        # plus a due `next_action_at`. This is the exact access pattern.
        Index("ix_outreach_state_next_action_at", "state", "next_action_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    founder_id: Mapped[int] = mapped_column(ForeignKey("founders.id"), unique=True)
    state: Mapped[str] = mapped_column(String(30), default="new")
    resolved_email: Mapped[str | None] = mapped_column(String(320))
    sequence_step: Mapped[int] = mapped_column(default=0)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the operator last passed over this draft in review (2026-09-22).
    # NULL means never skipped - a letter he has not reached is not the
    # same fact as one he looked at and moved on from.
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    founder: Mapped["Founder"] = relationship(back_populates="outreach")
    messages: Mapped[list["Message"]] = relationship(back_populates="outreach")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Load-bearing (spec §13): a double-send is structurally impossible
        # because the second "reserve" insert for the same (outreach_id,
        # sequence_step) outbound row fails this constraint, whether it
        # comes from a second worker or a retry after a crash. Scoped to
        # `direction = 'outbound'` via a partial index so inbound replies
        # (which are not sequence-numbered the same way) are never blocked
        # by it.
        Index(
            "uq_messages_outreach_step_outbound",
            "outreach_id",
            "sequence_step",
            unique=True,
            postgresql_where=text("direction = 'outbound'"),
        ),
        # Load-bearing (spec §13, D5): a message can never be marked sent
        # without having been approved first. This holds even if the
        # application layer has a bug, because the database itself refuses
        # the row.
        # NOTE on the `name=` values below: with a naming convention active
        # on `Base.metadata`, SQLAlchemy's "ck" template
        # ("ck_%(table_name)s_%(constraint_name)s") itself supplies the
        # `ck_messages_` prefix — passing an already-prefixed name here
        # would double it (`ck_messages_ck_messages_...`). The name given
        # to `CheckConstraint` is only ever the suffix.
        CheckConstraint(
            "sent_at IS NULL OR approved_at IS NOT NULL",
            name="sent_requires_approved",
        ),
        # The partial index above is only as good as `direction` actually
        # being 'outbound' or 'inbound' — an uncontrolled value (e.g.
        # 'Outbound', a typo) falls outside the partial predicate and
        # silently escapes deduplication. This CHECK closes that gap so the
        # double-send guarantee doesn't depend on every future caller
        # spelling the string identically (spec §7 defines the domain as
        # outbound | inbound).
        CheckConstraint(
            "direction IN ('outbound', 'inbound')",
            name="direction",
        ),
        # Re-polling the same Gmail window must be free (spec §5).
        Index("uq_messages_gmail_message_id", "gmail_message_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    outreach_id: Mapped[int] = mapped_column(ForeignKey("outreach.id"), index=True)
    sequence_step: Mapped[int]
    direction: Mapped[str] = mapped_column(String(10))  # outbound | inbound
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    # The formatted version of `body` (2026-09-23), or null when the letter
    # has no formatting. `body` is always derived from it, never the other
    # way round (`app.domain.rich_text`), and stays what everything reads.
    body_html: Mapped[str | None] = mapped_column(Text)
    # 2026-09-25: the line about the founder this letter was last given (0007).
    founder_line: Mapped[str | None] = mapped_column(Text)
    # 2026-09-25: the verified facts this letter was written from (0008).
    research: Mapped[dict | None] = mapped_column(JSONB)
    research_source_type: Mapped[str | None] = mapped_column(String(30))
    # news | launch | job_posting | long_description; null for inbound
    research_source_id: Mapped[int | None]
    gmail_message_id: Mapped[str | None] = mapped_column(String(255))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by_user: Mapped[bool] = mapped_column(default=False)
    # D22 voice critic — advisory only, never a gate.
    critic_verdict: Mapped[str | None] = mapped_column(String(20))
    critic_concerns: Mapped[dict | None] = mapped_column(JSON)
    critic_overall_note: Mapped[str | None] = mapped_column(String(200))
    # Reply classification (inbound rows only, spec §5).
    label: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None]
    rationale: Mapped[str | None] = mapped_column(String(300))
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Task 10: True only once an operator has hand-corrected this row's
    # label via POST /api/messages/{id}/label - never set by the automatic
    # classifier (ClassifyReply._classify_one never passes corrected=True).
    corrected: Mapped[bool] = mapped_column(default=False)

    outreach: Mapped["Outreach"] = relationship(back_populates="messages")


class ResumeProfile(Base):
    __tablename__ = "resume_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text)
    claims: Mapped[list | None] = mapped_column(JSONB)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GmailCredential(Base):
    """Task 15 (spec §14): the OAuth refresh token for the sending Gmail
    account lives in Postgres, not in a `token.json` file. One row per
    account (`account_email` unique). Only the *refresh* token is stored —
    the short-lived access token is fetched on demand by
    `app.adapters.gmail.GmailSender` and never persisted — and the OAuth
    client id/secret stay in the client-secrets JSON the operator points
    `GMAIL_CLIENT_SECRETS_PATH` at, so this row alone cannot mint tokens.
    Added in migration 0002."""

    __tablename__ = "gmail_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_email: Mapped[str] = mapped_column(String(320), unique=True)
    refresh_token: Mapped[str] = mapped_column(Text)
    token_uri: Mapped[str] = mapped_column(String(500), default="https://oauth2.googleapis.com/token")
    scopes: Mapped[list | None] = mapped_column(JSONB)
    # Phase 2 (spec §5): the Gmail history-API cursor reply polling resumes
    # from, so a poll only ever asks Gmail for what changed since last time.
    last_history_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


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


class AppSettings(Base):
    """Maps to the `settings` table (spec §7). Named `AppSettings`, not
    `Settings`, to avoid colliding with `app.config.Settings` (the typed
    env-var settings object) wherever both are imported in the same module.
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    daily_send_cap: Mapped[int] = mapped_column(default=20)
    send_window_start: Mapped[time | None] = mapped_column(Time())
    send_window_end: Mapped[time | None] = mapped_column(Time())
    followup_day_1: Mapped[int | None] = mapped_column(default=5)
    followup_day_2: Mapped[int | None] = mapped_column(default=12)
    signature: Mapped[str | None] = mapped_column(Text)
