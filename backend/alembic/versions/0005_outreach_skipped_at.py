"""applications view: outreach.skipped_at (letters passed over in review)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22

Operator request (2026-09-22): a page listing every application,
filterable by what happened to it - sent, rejected, skipped.

Two of those were already facts in the data (`sent`, `discarded`). The
third was not: pressing [s] in the review queue called
`ReviewOutreach.skip`, whose whole body was "leave the row exactly as it
is". A letter the operator had looked at and passed over was
indistinguishable from one he had never reached.

`skipped_at` records the last time he passed over a draft. Nullable
because "never skipped" is the honest answer for every existing row - a
timestamp default would invent a decision nobody made. Approving,
rejecting or redrafting clears it, since each of those IS the decision
the skip deferred.

Hand-written, like 0001-0004, to keep the naming convention consistent
with `app.adapters.db.models`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "outreach",
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("outreach", "skipped_at")
