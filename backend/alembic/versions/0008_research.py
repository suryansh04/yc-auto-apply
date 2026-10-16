"""company research: messages.research, companies.web_research

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25

Operator request (2026-09-25): research the company and founder on the
web, hand Sarvam only verified facts with their source URLs, and show
those facts to the operator next to the letter.

`messages.research` holds the verified facts a letter was written from
(JSON: facts with source URLs, the question, a note on anything unsure),
so the review screen can show exactly what each claim rests on.

`companies.web_research` caches what the web search found about the
company (Tavily's free tier is 1,000 searches a month), like
`founders.web_research` does for the founder.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("research", postgresql.JSONB(), nullable=True))
    op.add_column("companies", sa.Column("web_research", postgresql.JSONB(), nullable=True))
    op.add_column("companies", sa.Column("web_researched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("companies", "web_researched_at")
    op.drop_column("companies", "web_research")
    op.drop_column("messages", "research")
