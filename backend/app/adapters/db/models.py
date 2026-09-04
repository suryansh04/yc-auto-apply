"""SQLAlchemy 2.0 ORM models for the persistence schema (spec §7).

Scope note: this is Phase 1. The `outcomes` table and the
`messages.critic_verdict` / `critic_concerns` / `critic_overall_note`
columns belong to Phase 2 (the voice critic, D22) and are deliberately
NOT modelled here.

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
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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

    id: Mapped[int] = mapped_column(primary_key=True)
    founder_id: Mapped[int] = mapped_column(ForeignKey("founders.id"), unique=True)
    state: Mapped[str] = mapped_column(String(30), default="new")
    resolved_email: Mapped[str | None] = mapped_column(String(320))
    sequence_step: Mapped[int] = mapped_column(default=0)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        CheckConstraint(
            "sent_at IS NULL OR approved_at IS NOT NULL",
            name="ck_messages_sent_requires_approved",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    outreach_id: Mapped[int] = mapped_column(ForeignKey("outreach.id"), index=True)
    sequence_step: Mapped[int]
    direction: Mapped[str] = mapped_column(String(10))  # outbound | inbound
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    research_source_type: Mapped[str | None] = mapped_column(String(30))
    # news | launch | job_posting | long_description; null for inbound
    research_source_id: Mapped[int | None]
    gmail_message_id: Mapped[str | None] = mapped_column(String(255))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by_user: Mapped[bool] = mapped_column(default=False)

    outreach: Mapped["Outreach"] = relationship(back_populates="messages")


class ResumeProfile(Base):
    __tablename__ = "resume_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text)
    claims: Mapped[list | None] = mapped_column(JSONB)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
