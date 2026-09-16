"""phase 2 schema: jobs, critic verdicts, reply classification

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13

Task 1 (spec §4, §5, D22): adds the `jobs` table backing background
pipeline-stage runs; the voice-critic columns
(`critic_verdict`/`critic_concerns`/`critic_overall_note`) and reply
classification columns (`label`/`confidence`/`rationale`/`classified_at`)
on `messages`; a UNIQUE index on `messages.gmail_message_id` so re-polling
the same Gmail window can never duplicate a row; and
`gmail_credentials.last_history_id`, the Gmail history-API cursor reply
polling resumes from.

Hand-written, like 0001/0002, to keep the naming convention consistent
with `app.adapters.db.models`; verified with
`alembic upgrade head --sql` (rendered DDL checked by eye against
`Base.metadata`) and then applied for real against the docker-compose
Postgres on port 5433.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_jobs_status"),
        ),
        sa.CheckConstraint(
            "created_by IN ('ui', 'schedule', 'cli')",
            name=op.f("ck_jobs_created_by"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "uq_jobs_kind_active",
        "jobs",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.add_column(
        "messages",
        sa.Column("critic_verdict", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("critic_concerns", sa.JSON(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("critic_overall_note", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("label", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("rationale", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_messages_gmail_message_id",
        "messages",
        ["gmail_message_id"],
        unique=True,
    )

    op.add_column(
        "gmail_credentials",
        sa.Column("last_history_id", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("gmail_credentials", "last_history_id")

    op.drop_index("uq_messages_gmail_message_id", table_name="messages")
    op.drop_column("messages", "classified_at")
    op.drop_column("messages", "rationale")
    op.drop_column("messages", "confidence")
    op.drop_column("messages", "label")
    op.drop_column("messages", "critic_overall_note")
    op.drop_column("messages", "critic_concerns")
    op.drop_column("messages", "critic_verdict")

    op.drop_index("uq_jobs_kind_active", table_name="jobs")
    op.drop_table("jobs")
