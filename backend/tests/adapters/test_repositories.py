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
