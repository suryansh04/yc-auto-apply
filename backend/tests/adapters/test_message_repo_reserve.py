"""Task 15 (ruling R1): the real `MessageRepo.reserve_for_send` must be ONE
conditional UPDATE — `WHERE id = :id AND sent_at IS NULL AND approved_at IS
NOT NULL` — whose rowcount is returned, never a read-then-write. This runs
offline against a stub session that captures the statement and compiles it
for the PostgreSQL dialect, so the SQL shape is pinned without a database.
The integration suite (`-m integration`) is where the rowcount semantics
are proven against a real Postgres.
"""

import re
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql

from app.adapters.db.repositories import MessageRepo


class _Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount

    def scalar_one(self):
        return self.rowcount


class _StubSession:
    def __init__(self, rowcount=1):
        self.statements = []
        self._rowcount = rowcount

    def execute(self, statement):
        self.statements.append(statement)
        return _Result(self._rowcount)

    def get(self, *a, **kw):  # pragma: no cover - reserve must never read first
        raise AssertionError("reserve_for_send must not read the row before updating it")


def _sql(statement) -> str:
    return re.sub(r"\s+", " ", str(statement.compile(dialect=postgresql.dialect()))).strip()


def test_reserve_is_a_single_conditional_update_returning_rowcount():
    session = _StubSession(rowcount=1)
    now = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)

    assert MessageRepo(session).reserve_for_send(42, now) == 1

    assert len(session.statements) == 1
    sql = _sql(session.statements[0])
    assert sql.startswith("UPDATE messages SET sent_at=")
    assert "messages.id = " in sql
    assert "messages.sent_at IS NULL" in sql
    assert "messages.approved_at IS NOT NULL" in sql
    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert params["sent_at"] == now
    assert params["id_1"] == 42


def test_reserve_reports_a_lost_race_as_zero():
    session = _StubSession(rowcount=0)
    assert MessageRepo(session).reserve_for_send(42, datetime.now(timezone.utc)) == 0
    assert len(session.statements) == 1


def test_count_sent_since_counts_outbound_rows_from_the_cutoff():
    session = _StubSession(rowcount=3)
    since = datetime(2026, 9, 12, tzinfo=timezone.utc)
    assert MessageRepo(session).count_sent_since(since) == 3
    sql = _sql(session.statements[0])
    assert sql.startswith("SELECT count(*)")
    assert "messages.direction = " in sql
    assert "messages.sent_at >= " in sql
    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert params["direction_1"] == "outbound"
    assert params["sent_at_1"] == since
