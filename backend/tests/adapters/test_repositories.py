import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def test_duplicate_outbound_message_is_rejected(session, an_outreach):
    from app.adapters.db.models import Message
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b"))
    session.commit()
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s2", body="b2"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_sent_without_approved_is_rejected(session, an_outreach):
    from app.adapters.db.models import Message
    from datetime import datetime, timezone
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b",
                        sent_at=datetime.now(timezone.utc), approved_at=None))
    with pytest.raises(IntegrityError):
        session.commit()


# --- Fix round 1: review findings I1-I3 ---
#
# I1 added a CHECK constraint on `direction` (models.py / migration 0001)
# because the double-send guard's partial unique index is scoped
# `WHERE direction = 'outbound'` — any value that isn't the exact string
# 'outbound' silently falls outside that predicate and is never
# deduplicated. This test proves the CHECK constraint is what closes that
# gap, independent of the partial index.
def test_invalid_direction_is_rejected(session, an_outreach):
    from app.adapters.db.models import Message
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="Outbound", subject="s", body="b"))
    with pytest.raises(IntegrityError):
        session.commit()


# I2 — the deviation from a table-wide UniqueConstraint to a partial index
# scoped to outbound rows was justified by "an inbound reply must be able
# to coexist with the outbound message at the same step." That claim was
# never asserted anywhere. This test asserts it directly: an inbound row at
# the same (outreach_id, sequence_step) as an existing outbound row must
# commit cleanly. If a future change reverts to an unscoped unique
# constraint, this test — not just eyeballing the DDL — is what catches it.
def test_inbound_can_coexist_with_outbound_at_same_step(session, an_outreach):
    from app.adapters.db.models import Message
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b"))
    session.commit()
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="inbound", body="a reply came in"))
    session.commit()  # must not raise


# I3 — the two constraint tests above only pin the NEGATIVE half of each
# constraint. A CHECK constraint of `sent_at IS NULL` alone (forbidding
# every send outright) would also make test_sent_without_approved_is_rejected
# pass, and an unscoped-in-the-wrong-direction unique index could still
# reject legitimate rows. These positive-path tests pin the other half:
# rows the constraints must NOT reject.
def test_approved_and_sent_together_is_accepted(session, an_outreach):
    from app.adapters.db.models import Message
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b",
                        approved_at=now, sent_at=now))
    session.commit()  # must not raise


def test_approved_without_sent_is_accepted(session, an_outreach):
    from app.adapters.db.models import Message
    from datetime import datetime, timezone
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b",
                        approved_at=datetime.now(timezone.utc), sent_at=None))
    session.commit()  # must not raise


def test_second_message_at_different_step_is_accepted(session, an_outreach):
    from app.adapters.db.models import Message
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b"))
    session.commit()
    session.add(Message(outreach_id=an_outreach.id, sequence_step=1,
                        direction="outbound", subject="s2", body="b2"))
    session.commit()  # must not raise


# --- Final fix wave (F14): `JobPostingRepo.upsert` keys on `(company_id,
# title)` when the payload carries no `url`, so url-less postings at one
# company neither collapse into one row nor duplicate on re-scrape.

def test_url_less_postings_at_one_company_are_kept_apart_by_title(session, an_outreach):
    from app.adapters.db.models import JobPosting
    from app.adapters.db.repositories import JobPostingRepo
    from sqlalchemy import select

    repo = JobPostingRepo(session)
    company_id = an_outreach.founder.company_id
    a = repo.upsert(company_id=company_id, title="Backend Intern", url=None, employment_type="Internship")
    b = repo.upsert(company_id=company_id, title="Data Intern", url=None, employment_type="Internship")
    assert a.id != b.id

    again = repo.upsert(company_id=company_id, title="Backend Intern", url=None, employment_type="Intern")
    assert again.id == a.id and again.employment_type == "Intern"

    rows = session.execute(select(JobPosting).where(JobPosting.company_id == company_id)).scalars().all()
    assert sorted(r.title for r in rows) == ["Backend Intern", "Data Intern"]


def test_postings_with_a_url_still_key_on_the_url(session, an_outreach):
    from app.adapters.db.repositories import JobPostingRepo

    repo = JobPostingRepo(session)
    company_id = an_outreach.founder.company_id
    a = repo.upsert(company_id=company_id, title="Backend Intern", url="/jobs/1")
    b = repo.upsert(company_id=company_id, title="Backend Intern (renamed)", url="/jobs/1")
    assert b.id == a.id and b.title == "Backend Intern (renamed)"


# --- Task 11 review: OutreachRepo.list_due -----------------------------------
#
# Fix round 1: mutation testing on the real (SQLAlchemy) `list_due` found
# it had zero direct tests — deleting either the `state == SENT` filter or
# the (then-present) `next_action_at.is_not(None)` clause still left
# 802/802 passing, because only the fake mirror was ever exercised.
#
# Fix round 2 (ruling T11-3): `list_due` no longer filters on
# `next_action_at` at all — it caused the live-settings recompute
# (`ScheduleFollowUps`) to work in only one direction (a LENGTHENED
# interval correctly deferred a row, but a SHORTENED one could never
# accelerate one past a stale, still-future `next_action_at`, because the
# SQL excluded the row before the recompute ever ran). Now it returns
# every `SENT` row, full stop — due-ness, `next_action_at`'s value
# included or not, is entirely the application layer's call. These tests
# are updated to match: the positive case no longer needs a past
# `next_action_at` at all, the state filter is still directly pinned, and
# the NULL case is now a POSITIVE assertion (must be RETURNED, not
# excluded) — the safety net for a NULL anchor lives in
# `ScheduleFollowUps` itself (see
# `test_a_null_next_action_at_is_still_evaluated_via_sent_at` and
# `test_a_row_with_no_outbound_message_at_all_does_not_crash_the_recompute`
# in `tests/application/test_schedule_follow_ups.py`), not in this query.


def test_list_due_returns_a_sent_row_regardless_of_its_next_action_at(session, an_outreach):
    """No time filter here any more — a `SENT` row with a future
    `next_action_at` is still a candidate; `ScheduleFollowUps` decides
    due-ness live."""
    from datetime import datetime, timedelta, timezone

    from app.adapters.db.repositories import OutreachRepo

    an_outreach.state = "sent"
    an_outreach.next_action_at = datetime.now(timezone.utc) + timedelta(days=30)
    session.flush()

    due_ids = [o.id for o in OutreachRepo(session).list_due()]
    assert an_outreach.id in due_ids


def test_list_due_excludes_a_replied_row(session, an_outreach):
    """The `state == SENT` filter is the only filter left — a mutant that
    dropped it would find every state eligible."""
    from datetime import datetime, timedelta, timezone

    from app.adapters.db.repositories import OutreachRepo

    an_outreach.state = "replied"
    an_outreach.next_action_at = datetime.now(timezone.utc) - timedelta(days=1)
    session.flush()

    due_ids = [o.id for o in OutreachRepo(session).list_due()]
    assert an_outreach.id not in due_ids


def test_list_due_includes_a_null_next_action_at(session, an_outreach):
    """Fix round 2: a `SENT` row with `next_action_at IS NULL` is now
    RETURNED, not excluded — real Postgres NULL semantics no longer come
    into play at all, since there is no `next_action_at` clause left to
    evaluate them against. `ScheduleFollowUps` is what has to remain safe
    when handed a NULL anchor (pinned at the application layer, not
    here)."""
    from app.adapters.db.repositories import OutreachRepo

    an_outreach.state = "sent"
    an_outreach.next_action_at = None
    session.flush()

    due_ids = [o.id for o in OutreachRepo(session).list_due()]
    assert an_outreach.id in due_ids
