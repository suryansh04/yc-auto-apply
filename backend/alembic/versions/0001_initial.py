"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-04

Phase 1 schema (spec §7): companies, job_postings, founders, company_news,
company_launches, email_candidates, outreach, messages, resume_profile,
settings. The `outcomes` table and `messages.critic_*` columns are Phase 2
(D22) and are intentionally not created here.

Hand-written rather than produced by `alembic revision --autogenerate`:
this machine has no reachable Postgres credentials for the `yc`/`ycautoapply`
role/database (native Postgres on 127.0.0.1:5432 requires a password this
task was not given, and guessing further or altering that installation's
auth config was out of scope), so autogenerate — which must connect to
introspect the current DB — could not run. This file was written to match
`app.adapters.db.models.Base.metadata` exactly and was verified by eye
against the DDL `alembic upgrade head --sql` renders offline.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("yc_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("batch", sa.String(length=50), nullable=True),
        sa.Column("team_size", sa.Integer(), nullable=True),
        sa.Column("regions", postgresql.JSONB(), nullable=True),
        sa.Column("all_locations", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("stage", sa.String(length=50), nullable=True),
        sa.Column("is_hiring", sa.Boolean(), nullable=False),
        sa.Column("one_liner", sa.Text(), nullable=True),
        sa.Column("long_description", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("slug", name=op.f("uq_companies_slug")),
    )
    op.create_index(op.f("ix_companies_yc_id"), "companies", ["yc_id"], unique=True)

    op.create_table(
        "founders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("yc_user_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("twitter_url", sa.String(length=500), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name=op.f("fk_founders_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_founders")),
    )
    op.create_index(op.f("ix_founders_company_id"), "founders", ["company_id"], unique=False)
    op.create_index(op.f("ix_founders_yc_user_id"), "founders", ["yc_user_id"], unique=True)

    op.create_table(
        "job_postings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("employment_type", sa.String(length=50), nullable=True),
        sa.Column("role_specific_type", sa.String(length=100), nullable=True),
        sa.Column("salary_range", sa.String(length=100), nullable=True),
        sa.Column("min_experience", sa.String(length=100), nullable=True),
        sa.Column("skills", postgresql.JSONB(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name=op.f("fk_job_postings_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_postings")),
    )
    op.create_index(op.f("ix_job_postings_company_id"), "job_postings", ["company_id"], unique=False)

    op.create_table(
        "company_news",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name=op.f("fk_company_news_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_news")),
        sa.UniqueConstraint("company_id", "url", name=op.f("uq_company_news_company_id")),
    )
    op.create_index(op.f("ix_company_news_company_id"), "company_news", ["company_id"], unique=False)

    op.create_table(
        "company_launches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("published_email", sa.String(length=255), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name=op.f("fk_company_launches_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_launches")),
    )
    op.create_index(op.f("ix_company_launches_company_id"), "company_launches", ["company_id"], unique=False)

    op.create_table(
        "email_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("founder_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("pattern", sa.String(length=50), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(length=20), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["founder_id"], ["founders.id"],
            name=op.f("fk_email_candidates_founder_id_founders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_candidates")),
        sa.UniqueConstraint("founder_id", "address", name=op.f("uq_email_candidates_founder_id")),
    )
    op.create_index(op.f("ix_email_candidates_founder_id"), "email_candidates", ["founder_id"], unique=False)

    op.create_table(
        "outreach",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("founder_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("resolved_email", sa.String(length=320), nullable=True),
        sa.Column("sequence_step", sa.Integer(), nullable=False),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["founder_id"], ["founders.id"],
            name=op.f("fk_outreach_founder_id_founders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outreach")),
        sa.UniqueConstraint("founder_id", name=op.f("uq_outreach_founder_id")),
    )
    # Spec §7/§8: every stage worker claims `outreach` rows by state plus a
    # due `next_action_at`. This is the exact access pattern.
    op.create_index(
        op.f("ix_outreach_state_next_action_at"),
        "outreach",
        ["state", "next_action_at"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outreach_id", sa.Integer(), nullable=False),
        sa.Column("sequence_step", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("research_source_type", sa.String(length=30), nullable=True),
        sa.Column("research_source_id", sa.Integer(), nullable=True),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=True),
        sa.Column("gmail_thread_id", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_by_user", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["outreach_id"], ["outreach.id"],
            name=op.f("fk_messages_outreach_id_outreach"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        # LOAD-BEARING (spec §13, D5): a message marked sent must have been
        # approved. Enforced by the database, independent of the
        # application layer.
        sa.CheckConstraint(
            "sent_at IS NULL OR approved_at IS NOT NULL",
            name=op.f("ck_messages_sent_requires_approved"),
        ),
        # The partial unique index below is only as good as `direction`
        # actually being 'outbound' or 'inbound' — an uncontrolled value
        # falls outside the partial predicate and silently escapes
        # deduplication. Closes that gap (spec §7 defines the domain as
        # outbound | inbound).
        sa.CheckConstraint(
            "direction IN ('outbound', 'inbound')",
            name=op.f("ck_messages_direction"),
        ),
    )
    op.create_index(op.f("ix_messages_outreach_id"), "messages", ["outreach_id"], unique=False)
    # LOAD-BEARING (spec §13): makes a double-send of the same step on the
    # same outreach structurally impossible, even across a crash/retry.
    # Partial (direction = 'outbound') so inbound replies are never blocked
    # by it.
    op.create_index(
        "uq_messages_outreach_step_outbound",
        "messages",
        ["outreach_id", "sequence_step"],
        unique=True,
        postgresql_where=sa.text("direction = 'outbound'"),
    )

    op.create_table(
        "resume_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("claims", postgresql.JSONB(), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resume_profile")),
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("daily_send_cap", sa.Integer(), nullable=False),
        sa.Column("send_window_start", sa.Time(), nullable=True),
        sa.Column("send_window_end", sa.Time(), nullable=True),
        sa.Column("followup_day_1", sa.Integer(), nullable=True),
        sa.Column("followup_day_2", sa.Integer(), nullable=True),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("settings")
    op.drop_table("resume_profile")
    op.drop_index("uq_messages_outreach_step_outbound", table_name="messages")
    op.drop_index(op.f("ix_messages_outreach_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_outreach_state_next_action_at"), table_name="outreach")
    op.drop_table("outreach")
    op.drop_index(op.f("ix_email_candidates_founder_id"), table_name="email_candidates")
    op.drop_table("email_candidates")
    op.drop_index(op.f("ix_company_launches_company_id"), table_name="company_launches")
    op.drop_table("company_launches")
    op.drop_index(op.f("ix_company_news_company_id"), table_name="company_news")
    op.drop_table("company_news")
    op.drop_index(op.f("ix_job_postings_company_id"), table_name="job_postings")
    op.drop_table("job_postings")
    op.drop_index(op.f("ix_founders_yc_user_id"), table_name="founders")
    op.drop_index(op.f("ix_founders_company_id"), table_name="founders")
    op.drop_table("founders")
    op.drop_index(op.f("ix_companies_yc_id"), table_name="companies")
    op.drop_table("companies")
