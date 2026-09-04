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
