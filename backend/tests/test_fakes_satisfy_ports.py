from app import ports
from tests import fakes


def test_fakes_are_structural_subtypes():
    # Every Protocol in app.ports is @runtime_checkable, so isinstance()
    # actually inspects each fake's methods against the Protocol instead of
    # just checking truthiness (a fake with a renamed or missing method
    # would fail here, which a bare `assert obj` never would).
    assert isinstance(fakes.FakeYCDirectory(), ports.YCDirectory)
    assert isinstance(fakes.FakeCompanyPageSource(), ports.CompanyPageSource)
    assert isinstance(fakes.FakeVerifier({}), ports.EmailVerifier)
    assert isinstance(fakes.FakeDrafter(), ports.Drafter)
    assert isinstance(fakes.FakeResumeParser(), ports.ResumeParser)
    assert isinstance(fakes.FakeSender(), ports.MailSender)
    assert isinstance(fakes.FrozenClock(), ports.Clock)


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
