from app.application.resolve_emails import ResolveEmails
from app.domain.states import OutreachState as S
from app.ports import VerifierError
from tests.fakes import FakeVerifier, FrozenClock

# --- The brief's own 5 tests, verbatim — left untouched by fix round 1.

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


# --- Fix round 1 (review of commit `c6c0e8e`): mutation-killing and new
# behaviour coverage. The review's mutation run found 7/7 survivors against
# the tests above alone — each test below is aimed at exactly one of them,
# plus the new empty-domain/VerifierError/Clock/attribution behaviour.

def test_already_verified_candidate_is_not_re_verified(repos, a_founder):
    # Kills: disabling `already`-verified reuse. A prior run's persisted
    # verdict for the first-priority candidate must short-circuit this
    # run's search for it without spending a verification call.
    repos.email_candidate.upsert(
        founder_id=a_founder.id, address="pratish@onefin.com",
        pattern="first", priority=1, verdict="valid", provider="myemailverifier",
    )
    v = FakeVerifier({})
    result = ResolveEmails(v, repos).run()

    assert v.calls == []
    assert result.verifications_used == 0
    assert repos.outreach.get(a_founder.id).resolved_email == "pratish@onefin.com"


def test_unknown_then_valid_resolves_to_the_valid_candidate(repos, a_founder):
    # Kills: treating `unknown` as a stop instead of "keep trying the next
    # candidate" — no application test previously produced an `unknown`
    # verdict at all.
    v = FakeVerifier({"pratish@onefin.com": "unknown",
                      "pratish.gandhi@onefin.com": "valid"})
    ResolveEmails(v, repos).run()

    assert v.calls == ["pratish@onefin.com", "pratish.gandhi@onefin.com"]
    assert repos.outreach.get(a_founder.id).resolved_email == "pratish.gandhi@onefin.com"


def test_limit_processes_exactly_one_founder(repos, two_founders):
    # Kills: ignoring `limit`.
    f1, f2 = two_founders
    result = ResolveEmails(FakeVerifier({}), repos).run(limit=1)

    assert repos.outreach.get(f1.id).state == S.NEEDS_MANUAL_EMAIL  # processed
    assert repos.outreach.get(f2.id).state == S.NEEDS_EMAIL         # untouched
    assert result.manual + result.resolved + result.errors == 1


def test_quota_pause_leaves_the_paused_founder_resumable_not_manual(repos, two_founders):
    # Kills: moving the mid-quota founder to NEEDS_MANUAL_EMAIL on pause —
    # the brief's own test only asserted `!= FAILED`, which a
    # NEEDS_MANUAL_EMAIL-on-pause bug would still satisfy.
    f1, f2 = two_founders
    v = FakeVerifier({}, quota=1)

    result = ResolveEmails(v, repos).run()

    assert result.paused_on_quota is True
    assert repos.outreach.get(f1.id).state == S.NEEDS_EMAIL  # mid-search when quota hit
    assert repos.outreach.get(f2.id).state == S.NEEDS_EMAIL  # never even started


def test_catch_all_calls_only_the_one_candidate_that_came_back_catch_all(repos, a_founder):
    # Kills: removing the `catch_all` early `break`.
    v = FakeVerifier({"pratish@onefin.com": "catch_all"})
    ResolveEmails(v, repos).run()
    assert v.calls == ["pratish@onefin.com"]


def test_empty_company_domain_skips_verification_and_goes_to_manual(repos):
    repos.company.add(id=20, domain=None)
    founder = repos.founder.add(id=20, company_id=20, full_name="No Domain Founder")
    repos.outreach.add(id=20, founder_id=founder.id, state=str(S.NEEDS_EMAIL))

    v = FakeVerifier({})
    ResolveEmails(v, repos).run()

    assert v.calls == []
    assert repos.outreach.get(20).state == S.NEEDS_MANUAL_EMAIL


def test_persisted_verdict_is_stamped_with_verified_at_from_the_clock(repos, a_founder):
    clock = FrozenClock()
    v = FakeVerifier({"pratish@onefin.com": "valid"})

    ResolveEmails(v, repos, clock=clock).run()

    rows = repos.email_candidate.list_by_founder_id(a_founder.id)
    assert rows
    assert all(row.verified_at == clock.now() for row in rows)


class _RaisingOnceVerifier:
    """Test-only verifier: raises `VerifierError` for one specific address,
    otherwise defers to a normal `FakeVerifier`. Exercises the per-founder
    `VerifierError` path (fix round 1) without going through the real
    adapter."""

    def __init__(self, fail_address: str, verdicts: dict | None = None):
        self._fail_address = fail_address
        self._inner = FakeVerifier(verdicts or {})
        self.calls: list[str] = []

    def verify(self, address: str) -> str:
        self.calls.append(address)
        if address == self._fail_address:
            raise VerifierError(address, status=500)
        return self._inner.verify(address)

    def remaining_quota(self):
        return None


def test_verifier_error_leaves_the_founder_needs_email_and_the_run_continues(repos, two_founders):
    f1, f2 = two_founders  # f1 = Asha Rao @ one.com, f2 = Vikram Shah @ two.com
    v = _RaisingOnceVerifier(fail_address="asha@one.com", verdicts={"vikram@two.com": "valid"})

    result = ResolveEmails(v, repos).run()

    assert result.errors == 1
    assert result.paused_on_quota is False
    assert repos.outreach.get(f1.id).state == S.NEEDS_EMAIL       # left resumable, not FAILED
    assert repos.outreach.get(f2.id).state == S.READY_TO_DRAFT    # run continued past the error
    assert repos.outreach.get(f2.id).resolved_email == "vikram@two.com"


# --- Fix round 1: published-email attribution — `\b`-bounded regex over
# first name or full name only, within ~200 chars of the address, never a
# bare last name (spec §9 Step 0(b): attribute only to the right founder).

def test_attribution_matches_when_first_name_is_near_the_published_address(repos):
    repos.company.add(id=10, domain="co.com")
    f1 = repos.founder.add(id=10, company_id=10, full_name="Amit Kumar")
    f2 = repos.founder.add(id=11, company_id=10, full_name="Priya Nair")
    repos.outreach.add(id=10, founder_id=f1.id, state=str(S.NEEDS_EMAIL))
    repos.outreach.add(id=11, founder_id=f2.id, state=str(S.NEEDS_EMAIL))
    repos.launch.add(company_id=10, body="Amit here, reach me at amit@co.com",
                      published_email="amit@co.com")

    ResolveEmails(FakeVerifier({}), repos).run()

    o1 = repos.outreach.get(10)
    assert o1.state == S.READY_TO_DRAFT
    assert o1.resolved_email == "amit@co.com"


def test_attribution_is_withheld_and_persisted_as_a_hint_when_name_is_not_near_the_address(repos):
    repos.company.add(id=12, domain="co.com")
    f1 = repos.founder.add(id=12, company_id=12, full_name="Amit Kumar")
    f2 = repos.founder.add(id=13, company_id=12, full_name="Priya Nair")
    repos.outreach.add(id=12, founder_id=f1.id, state=str(S.NEEDS_EMAIL))
    repos.outreach.add(id=13, founder_id=f2.id, state=str(S.NEEDS_EMAIL))
    repos.launch.add(company_id=12, body="Amit here, reach me at amit@co.com",
                      published_email="amit@co.com")

    ResolveEmails(FakeVerifier({}), repos).run()

    o2 = repos.outreach.get(13)
    assert o2.resolved_email is None
    assert o2.state == S.NEEDS_MANUAL_EMAIL  # falls through to normal candidate search
    hints = [c for c in repos.email_candidate.list_by_founder_id(f2.id) if c.verdict == "published"]
    assert len(hints) == 1
    assert hints[0].address == "amit@co.com"


def test_bare_surname_or_short_first_name_substrings_never_falsely_attribute(repos):
    # Reproduces the review's own false positives: "Ng" inside "building",
    # "Al" inside "small" and "Park" inside "marketplace" must never count
    # as a name match.
    repos.company.add(id=14, domain="co.com")
    f1 = repos.founder.add(id=14, company_id=14, full_name="Amit Kumar")
    f2 = repos.founder.add(id=15, company_id=14, full_name="Riya Ng")
    f3 = repos.founder.add(id=16, company_id=14, full_name="Al Park")
    repos.outreach.add(id=14, founder_id=f1.id, state=str(S.NEEDS_EMAIL))
    repos.outreach.add(id=15, founder_id=f2.id, state=str(S.NEEDS_EMAIL))
    repos.outreach.add(id=16, founder_id=f3.id, state=str(S.NEEDS_EMAIL))
    repos.launch.add(
        company_id=14,
        body="We are building a small idea for a new marketplace — reach amit@co.com",
        published_email="amit@co.com",
    )

    ResolveEmails(FakeVerifier({}), repos).run()

    assert repos.outreach.get(15).resolved_email is None  # Riya Ng
    assert repos.outreach.get(16).resolved_email is None  # Al Park


def test_attribution_requires_proximity_to_the_address_not_just_presence_in_the_body(repos):
    repos.company.add(id=17, domain="co.com")
    f1 = repos.founder.add(id=17, company_id=17, full_name="Amit Kumar")
    f2 = repos.founder.add(id=18, company_id=17, full_name="Priya Rao")
    repos.outreach.add(id=17, founder_id=f1.id, state=str(S.NEEDS_EMAIL))
    repos.outreach.add(id=18, founder_id=f2.id, state=str(S.NEEDS_EMAIL))
    far_body = "Priya says hello. " + ("filler text " * 40) + "Reach us at amit@co.com for questions."
    repos.launch.add(company_id=17, body=far_body, published_email="amit@co.com")

    ResolveEmails(FakeVerifier({}), repos).run()

    assert repos.outreach.get(18).resolved_email is None  # "Priya" is far outside the window


# --- Task 16 (offline e2e surfaced it): `ScrapeCompanyPage` creates every
# new founder's outreach row in `NEW` (spec §8 stage 2), but nothing moved
# it to `NEEDS_EMAIL`, so `sync -> scrape -> resolve` resolved nobody.
# `run` now picks `NEW` rows up too, stepping them `NEW -> NEEDS_EMAIL`
# through the state machine before attempting resolution.

def test_a_new_founder_is_picked_up_and_resolved(repos, a_founder):
    repos.outreach.get(a_founder.id).state = str(S.NEW)
    v = FakeVerifier({"pratish@onefin.com": "valid"})
    result = ResolveEmails(v, repos).run()
    assert result.resolved == 1
    o = repos.outreach.get(a_founder.id)
    assert o.state == S.READY_TO_DRAFT and o.resolved_email == "pratish@onefin.com"


def test_a_new_founder_paused_by_quota_is_left_needs_email_not_new(repos, a_founder):
    """The NEW -> NEEDS_EMAIL step happens before the first verification,
    so a quota pause leaves the row resumable in NEEDS_EMAIL."""
    repos.outreach.get(a_founder.id).state = str(S.NEW)
    result = ResolveEmails(FakeVerifier({}, quota=0), repos).run()
    assert result.paused_on_quota is True
    assert repos.outreach.get(a_founder.id).state == S.NEEDS_EMAIL


def test_needs_email_rows_are_processed_before_new_ones(repos, two_founders):
    f1, f2 = two_founders
    repos.outreach.get(f1.id).state = str(S.NEW)          # f1 NEW, f2 NEEDS_EMAIL
    v = FakeVerifier({"asha@one.com": "valid", "vikram@two.com": "valid"})
    result = ResolveEmails(v, repos).run(limit=1)
    assert result.resolved == 1
    assert repos.outreach.get(f2.id).state == S.READY_TO_DRAFT
    assert repos.outreach.get(f1.id).state == S.NEW


def test_a_new_founder_hits_the_published_email_short_circuit(repos, founder_with_launch_email):
    """Task 16 fix round 1 (F5): the NEW -> NEEDS_EMAIL step precedes the
    D18 short circuit, so `NEEDS_EMAIL -> READY_TO_DRAFT` is legal there
    too - and no verification is spent."""
    repos.outreach.get(founder_with_launch_email.id).state = str(S.NEW)
    v = FakeVerifier({})
    result = ResolveEmails(v, repos).run()
    assert v.calls == [] and result.verifications_used == 0
    o = repos.outreach.get(founder_with_launch_email.id)
    assert o.state == S.READY_TO_DRAFT and o.resolved_email == "jane@acme.com"


# --- Final fix wave (F4, ruling FR-3): one commit per founder, so a Ctrl-C
# mid-run keeps every verdict already paid for out of the daily quota.

def test_run_commits_once_per_founder_processed(repos, two_founders):
    v = FakeVerifier({"asha@one.com": "valid", "vikram@two.com": "valid"})
    result = ResolveEmails(v, repos).run()
    assert result.resolved == 2
    assert repos.session.commit_calls == 2


def test_a_published_email_and_a_no_domain_founder_each_commit_too(repos, founder_with_launch_email):
    repos.company.add(id=9, domain=None)
    f = repos.founder.add(id=9, company_id=9, full_name="No Domain")
    repos.outreach.add(id=9, founder_id=f.id, state=str(S.NEEDS_EMAIL))
    ResolveEmails(FakeVerifier({}), repos).run()
    assert repos.session.commit_calls == 2


def test_a_quota_pause_mid_run_has_already_committed_the_earlier_founders(repos, two_founders):
    f1, f2 = two_founders
    # Enough credit for f1 (one valid) plus one call for f2, then exhaustion.
    v = FakeVerifier({"asha@one.com": "valid"}, quota=2)
    result = ResolveEmails(v, repos).run()
    assert result.paused_on_quota is True
    assert repos.outreach.get(f1.id).state == S.READY_TO_DRAFT
    assert repos.outreach.get(f2.id).state == S.NEEDS_EMAIL
    # f1 committed after resolution; the pause commits f2's partial verdicts too.
    assert repos.session.commit_calls == 2
    assert [c.address for c in repos.email_candidate.list_by_founder_id(f2.id)] == ["vikram@two.com"]


def test_a_verifier_error_commits_what_that_founder_already_had(repos, two_founders):
    f1, f2 = two_founders
    v = _RaisingOnceVerifier(fail_address="asha@one.com", verdicts={"vikram@two.com": "valid"})
    result = ResolveEmails(v, repos).run()
    assert result.errors == 1
    assert repos.session.commit_calls == 2   # once for the errored founder, once for the resolved one


# --- Final fix wave (F8): regression protection for two rulings whose
# mutations survived the whole-branch review (M24/M25).

def test_a_bare_surname_as_a_whole_word_near_the_address_never_attributes(repos):
    """M24: adding bare-last-name matching to `_name_near_address` must
    fail. "Ng" appears as its own word right next to the address and the
    founder is Riya Ng - still not hers (spec §9 Step 0(b): first name or
    full name only)."""
    repos.company.add(id=40, domain="co.com")
    f1 = repos.founder.add(id=40, company_id=40, full_name="Amit Kumar")
    f2 = repos.founder.add(id=41, company_id=40, full_name="Riya Ng")
    repos.outreach.add(id=40, founder_id=f1.id, state=str(S.NEEDS_EMAIL))
    repos.outreach.add(id=41, founder_id=f2.id, state=str(S.NEEDS_EMAIL))
    repos.launch.add(company_id=40, body="Ng here - write to amit@co.com any time.",
                     published_email="amit@co.com")

    ResolveEmails(FakeVerifier({}), repos).run()

    o = repos.outreach.get(41)
    assert o.resolved_email is None
    assert o.state == S.NEEDS_MANUAL_EMAIL
    assert [c.address for c in repos.email_candidate.list_by_founder_id(f2.id) if c.verdict == "published"] == ["amit@co.com"]


def test_a_surname_hidden_inside_another_word_near_the_address_never_attributes(repos):
    repos.company.add(id=42, domain="co.com")
    f1 = repos.founder.add(id=42, company_id=42, full_name="Amit Kumar")
    f2 = repos.founder.add(id=43, company_id=42, full_name="Riya Ng")
    repos.outreach.add(id=42, founder_id=f1.id, state=str(S.NEEDS_EMAIL))
    repos.outreach.add(id=43, founder_id=f2.id, state=str(S.NEEDS_EMAIL))
    repos.launch.add(company_id=42, body="We are building something - reach amit@co.com",
                     published_email="amit@co.com")

    ResolveEmails(FakeVerifier({}), repos).run()

    assert repos.outreach.get(43).resolved_email is None


class _BuggyVerifier:
    """A verifier with a bug of its own: neither `QuotaExhausted` nor
    `VerifierError`, just a bare `RuntimeError`."""

    def verify(self, address: str) -> str:
        raise RuntimeError("something unrelated broke")

    def remaining_quota(self):
        return None


def test_a_bare_runtime_error_from_the_verifier_propagates_out_of_run(repos, a_founder):
    """M25: widening `except QuotaExhausted` to `except (QuotaExhausted,
    RuntimeError)` would turn a real bug into a silent quota pause."""
    import pytest

    with pytest.raises(RuntimeError, match="something unrelated broke"):
        ResolveEmails(_BuggyVerifier(), repos).run()
    assert repos.outreach.get(a_founder.id).state == S.NEEDS_EMAIL
