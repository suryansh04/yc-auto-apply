"""task 10: messages.corrected (hand-corrected reply labels)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14

Task 10 (spec §5/§16, T9-1): adds `messages.corrected`, a boolean the
operator's label-correction route (`POST /api/messages/{id}/label`) stamps
`True` — never set by the automatic classifier (`ClassifyReply`) — so the
thread view (`GET /api/outreach/{id}/thread`) can show which labels are
the model's own and which an operator overrode. `NOT NULL DEFAULT false`
so every existing row (every reply classified before this migration) is
unambiguously "not corrected", not `NULL`.

Hand-written, like 0001/0002/0003, to keep the naming convention
consistent with `app.adapters.db.models`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "messages",
        sa.Column("corrected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "corrected")
