"""formatted letters: messages.body_html

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23

Operator request (2026-09-23): bold, italic, text size and lists in the
letter editor.

`body` stays the canonical plain text - the checks, the anchors that find
the company paragraph and the plain-text part of the email all read it.
`body_html` is the formatted version, set only when the letter actually
has formatting (`app.domain.rich_text.has_formatting`), so it is null for
every existing row and for every letter nobody formats. A null here means
"send it as plain text, exactly as before".

Hand-written, like 0001-0005, to keep the naming convention consistent
with `app.adapters.db.models`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("body_html", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "body_html")
