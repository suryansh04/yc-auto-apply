"""Fixtures for the integration-only repository/constraint tests.

These connect to a real Postgres database (via `app.config.get_settings()`.
`database_url`) and are only ever collected when `-m integration` is passed
explicitly — `pyproject.toml`'s `addopts = "-m 'not integration'"` deselects
this whole package by default, so importing SQLAlchemy/Postgres machinery
here never affects the offline suite.

Each test runs inside its own transaction that is rolled back on teardown,
so tests never need to truncate tables between runs.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings


@pytest.fixture
def db_engine():
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
