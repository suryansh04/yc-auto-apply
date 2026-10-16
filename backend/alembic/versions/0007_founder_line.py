"""the founder line: messages.founder_line, founders.web_research

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25

Operator request (2026-09-25): a line about the founder personally - "I
found your ML background really interesting" - written by its own agent
from the founder's YC bio, or a web search when the bio is thin.

`messages.founder_line` is the line the letter was last given. It sits
inside the company part of the body, so a swap of that part (the AI
suggestion) needs it to keep the line, and a swap of the line needs it to
find the old one.

`founders.web_research` caches what the web search found, so pressing
"Another" does not spend another search (Tavily's free tier is 1,000 a
month). `web_researched_at` says when; a founder searched once with
nothing found keeps an empty string, which is also an answer.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("founder_line", sa.Text(), nullable=True))
    op.add_column("founders", sa.Column("web_research", sa.Text(), nullable=True))
    op.add_column("founders", sa.Column("web_researched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("founders", "web_researched_at")
    op.drop_column("founders", "web_research")
    op.drop_column("messages", "founder_line")
