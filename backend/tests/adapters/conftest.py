"""Fixtures for the integration-only repository/constraint tests.

These connect to a real Postgres database (via `app.config.get_settings()`.
`database_url`) and are only ever collected when `-m integration` is passed
explicitly — `pyproject.toml`'s `addopts = "-m 'not integration'"` deselects
this whole package by default, so importing SQLAlchemy/Postgres machinery
here never affects the offline suite.

Each test runs inside its own transaction that is rolled back on teardown,
so tests never need to truncate tables between runs.

Without a `DATABASE_URL` in the ENVIRONMENT every test here SKIPS rather
than errors (Phase 1 final fix wave, F5; spec §15 "skip cleanly without
credentials") - the check runs before `get_settings()`, whose
`ValidationError` on the missing field used to surface as 7 errors under
`pytest -m integration`. Phase 2 final fix wave (F11): the check reads
`os.environ` ONLY, deliberately not `backend/.env` - `tests/conftest.py`'s
autouse `_ignore_operator_dotenv` strips the env file from `Settings` for
every test, so a `.env` on disk is invisible to `get_settings()` here and
honouring it in the predicate turned "skipped" into 12 errors + 1 failure.
Run with `DATABASE_URL=... pytest -m integration` to opt in.

PREREQUISITE: `alembic upgrade head` must have been run against the target
database before these tests are collected for real. These fixtures do not
create the schema themselves — an empty (unmigrated) database currently
fails with an opaque `sqlalchemy.exc.ProgrammingError: ... UndefinedTable`
the first time a fixture inserts a row, which does not obviously point back
to "run the migration first." If you see that error, run
`alembic upgrade head` (against the same `DATABASE_URL`) and retry.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings


def _database_url_configured() -> bool:
    """`DATABASE_URL` from the environment - the ONLY place `Settings` can
    read it from under the suite's autouse `_ignore_operator_dotenv`
    fixture - without instantiating `Settings` (which raises when it is
    absent). Never consults `.env` (F11): see the module docstring."""
    return bool(os.environ.get("DATABASE_URL"))


@pytest.fixture
def db_engine():
    if not _database_url_configured():
        pytest.skip("DATABASE_URL not set")
    engine = create_engine(get_settings().database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def an_outreach(session):
    from app.adapters.db.models import Company, Founder, Outreach

    company = Company(yc_id=1, slug="acme", name="Acme")
    session.add(company)
    session.flush()

    founder = Founder(company_id=company.id, yc_user_id=1, full_name="Jane Founder")
    session.add(founder)
    session.flush()

    outreach = Outreach(founder_id=founder.id, state="ready_to_draft")
    session.add(outreach)
    session.flush()

    return outreach
