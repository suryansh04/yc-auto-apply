from app.application.resolve_emails import ResolveEmails
from app.domain.states import OutreachState as S
from tests.fakes import FakeVerifier


def test_published_email_short_circuits_without_verification(repos, founder_with_launch_email):
    v = FakeVerifier({})
    result = ResolveEmails(v, repos).run()
    assert v.calls == []                      # no verification spent
    assert result.verifications_used == 0
    assert repos.outreach.get(founder_with_launch_email.id).state == S.READY_TO_DRAFT


def test_stops_at_first_valid_candidate(repos, a_founder):
    v = FakeVerifier({"pratish@onefin.com": "invalid",
                      "pratish.gandhi@onefin.com": "valid"})
    ResolveEmails(v, repos).run()
    assert v.calls == ["pratish@onefin.com", "pratish.gandhi@onefin.com"]
    assert repos.outreach.get(a_founder.id).resolved_email == "pratish.gandhi@onefin.com"


def test_all_invalid_routes_to_manual(repos, a_founder):
    ResolveEmails(FakeVerifier({}), repos).run()
    assert repos.outreach.get(a_founder.id).state == S.NEEDS_MANUAL_EMAIL


def test_catch_all_never_guesses(repos, a_founder):
    v = FakeVerifier({"pratish@onefin.com": "catch_all"})
    ResolveEmails(v, repos).run()
    o = repos.outreach.get(a_founder.id)
    assert o.state == S.NEEDS_MANUAL_EMAIL
    assert o.resolved_email is None


def test_quota_exhaustion_pauses_rather_than_failing(repos, two_founders):
    v = FakeVerifier({}, quota=1)
    result = ResolveEmails(v, repos).run()
    assert result.paused_on_quota is True
    assert all(o.state != S.FAILED for o in repos.outreach.all())
