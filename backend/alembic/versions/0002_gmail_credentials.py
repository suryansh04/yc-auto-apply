"""gmail credentials

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12

Task 15 (spec §14 "OAuth refresh tokens are stored in Postgres, not in
files"): the `gmail_credentials` table `app.cli.gmail_auth` writes and
`app.cli.send` reads. One row per sending account; only the refresh token
is stored (see `app.adapters.db.models.GmailCredential`).

Hand-written, like 0001, because this machine still has no reachable
Postgres to autogenerate against; verified offline with
`alembic upgrade 0001:0002 --sql` (rendered DDL checked by eye against
`Base.metadata`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "gmail_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("token_uri", sa.String(length=500), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gmail_credentials")),
        sa.UniqueConstraint("account_email", name=op.f("uq_gmail_credentials_account_email")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("gmail_credentials")
