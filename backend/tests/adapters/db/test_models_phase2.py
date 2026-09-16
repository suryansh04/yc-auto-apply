"""Phase 2 schema: jobs, critic columns, classification columns."""
import pytest
from sqlalchemy import inspect

from app.adapters.db.models import Base, GmailCredential, Job, Message


def test_job_table_columns():
    cols = {c.name for c in Job.__table__.columns}
    assert cols == {
        "id", "kind", "status", "created_by", "started_at", "finished_at",
        "done", "total", "summary", "error",
    }


def test_only_one_active_job_per_kind_is_possible():
    # A partial unique index, not application logic: a double-clicked button
    # must lose to the database.
    idx = {i.name: i for i in Job.__table__.indexes}
    active = idx["uq_jobs_kind_active"]
    assert active.unique is True
    assert [c.name for c in active.columns] == ["kind"]
    assert "queued" in str(active.dialect_options["postgresql"]["where"])


def test_job_status_is_constrained():
    checks = [c for c in Job.__table__.constraints if hasattr(c, "sqltext")]
    text = " ".join(str(c.sqltext) for c in checks)
    for status in ("queued", "running", "succeeded", "failed"):
        assert status in text


def test_message_gains_critic_and_classification_columns():
    cols = {c.name for c in Message.__table__.columns}
    assert {"critic_verdict", "critic_concerns", "critic_overall_note"} <= cols
    assert {"label", "confidence", "rationale", "classified_at"} <= cols


def test_gmail_message_id_is_unique_so_repolling_cannot_duplicate():
    uniques = [i for i in Message.__table__.indexes if i.unique]
    assert any([c.name for c in i.columns] == ["gmail_message_id"] for i in uniques)


def test_credential_stores_the_history_cursor():
    assert "last_history_id" in {c.name for c in GmailCredential.__table__.columns}
