from app import ports
from tests import fakes


def test_fakes_are_structural_subtypes():
    v: ports.EmailVerifier = fakes.FakeVerifier({})
    d: ports.Drafter = fakes.FakeDrafter()
    s: ports.MailSender = fakes.FakeSender()
    c: ports.Clock = fakes.FrozenClock()
    assert v and d and s and c


def test_fake_verifier_records_calls_and_respects_quota():
    v = fakes.FakeVerifier({"a@x.com": "valid"}, quota=1)
    assert v.verify("a@x.com") == "valid"
    assert v.calls == ["a@x.com"]
    assert v.remaining_quota() == 0


def test_fake_sender_returns_ids_and_records_sends():
    s = fakes.FakeSender()
    mid, tid = s.send("a@x.com", "subject here now", "body", None)
    assert mid and tid
    assert s.sent[0]["to"] == "a@x.com"
