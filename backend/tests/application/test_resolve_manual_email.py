"""Final fix wave (F3, ruling FR-1): `ResolveManualEmail` - the operator's
path out of `NEEDS_MANUAL_EMAIL`, driven by `list-manual` / `set-email`.
"""

import pytest

from app.application.resolve_manual_email import ResolveManualEmail, normalise_address
from app.domain.states import IllegalTransition
from app.domain.states import OutreachState as S


@pytest.fixture
def manual_founders(repos):
    """Two NEEDS_MANUAL_EMAIL founders: Priya at Acme (three candidates
    judged, plus a published-email hint from a launch that could not be
    attributed to her) and Vikram at Zed (no domain, nothing tried). A
    third founder, Asha, is READY_TO_DRAFT and must never be listed."""
    repos.company.add(id=1, domain="acme.com", name="Acme")
    priya = repos.founder.add(id=1, company_id=1, full_name="Priya Nair",
                              linkedin_url="https://linkedin.com/in/priya")
    repos.outreach.add(id=1, founder_id=priya.id, state=str(S.NEEDS_MANUAL_EMAIL))
    for priority, (address, verdict) in enumerate(
        [("priya@acme.com", "invalid"), ("priya.nair@acme.com", "invalid"), ("pnair@acme.com", "catch_all")], 1
    ):
        repos.email_candidate.upsert(founder_id=priya.id, address=address, pattern="p",
                                     priority=priority, verdict=verdict, provider="fake")
    repos.email_candidate.upsert(founder_id=priya.id, address="amit@acme.com", pattern=None,
                                 priority=0, verdict="published", provider="fake")

    repos.company.add(id=2, domain=None, name="Zed")
    vikram = repos.founder.add(id=2, company_id=2, full_name="Vikram Shah")
    repos.outreach.add(id=2, founder_id=vikram.id, state=str(S.NEEDS_MANUAL_EMAIL))

    repos.company.add(id=3, domain="three.com", name="Three")
    asha = repos.founder.add(id=3, company_id=3, full_name="Asha Rao")
    repos.outreach.add(id=3, founder_id=asha.id, state=str(S.READY_TO_DRAFT))
    return priya, vikram, asha


# --- pending() ---------------------------------------------------------------


def test_pending_lists_only_needs_manual_email_founders_ordered_by_company(repos, manual_founders):
    items = ResolveManualEmail(repos).pending()
    assert [i.founder.full_name for i in items] == ["Priya Nair", "Vikram Shah"]   # Acme < Zed
    assert [i.outreach.id for i in items] == [1, 2]


def test_pending_carries_candidates_in_priority_order_and_the_hint_separately(repos, manual_founders):
    priya_item = ResolveManualEmail(repos).pending()[0]
    assert [(c.address, c.verdict) for c in priya_item.candidates] == [
        ("priya@acme.com", "invalid"), ("priya.nair@acme.com", "invalid"), ("pnair@acme.com", "catch_all"),
    ]
    assert priya_item.published_hint == "amit@acme.com"
    assert priya_item.company.domain == "acme.com"
    assert priya_item.founder.linkedin_url == "https://linkedin.com/in/priya"


def test_pending_item_for_a_founder_with_nothing_tried_has_no_candidates_and_no_hint(repos, manual_founders):
    vikram_item = ResolveManualEmail(repos).pending()[1]
    assert vikram_item.candidates == [] and vikram_item.published_hint is None
    assert vikram_item.company.domain is None


def test_pending_is_empty_when_nobody_needs_a_manual_email(repos, a_founder):
    assert ResolveManualEmail(repos).pending() == []


# --- set_email() -------------------------------------------------------------


def test_set_email_stores_the_address_and_moves_to_ready_to_draft(repos, manual_founders):
    stored = ResolveManualEmail(repos).set_email(1, "Priya.N@Acme.com ")
    o = repos.outreach.get(1)
    assert stored == "priya.n@acme.com"
    assert o.resolved_email == "priya.n@acme.com"
    assert o.state == S.READY_TO_DRAFT
    assert o.last_error is None
    assert repos.session.commit_calls == 0   # the CLI owns the commit


@pytest.mark.parametrize("bad", ["", "   ", "priya", "priya@", "@acme.com", "priya@acme", "pri ya@acme.com",
                                 "priya@@acme.com", "priya@acme.com extra"])
def test_set_email_rejects_an_address_that_does_not_look_like_one(repos, manual_founders, bad):
    with pytest.raises(ValueError, match="does not look like an email address"):
        ResolveManualEmail(repos).set_email(1, bad)
    o = repos.outreach.get(1)
    assert o.state == S.NEEDS_MANUAL_EMAIL and o.resolved_email is None


def test_normalise_address_lowercases_and_strips():
    assert normalise_address("  Jane.Doe@Example.IO ") == "jane.doe@example.io"


@pytest.mark.parametrize("state", [S.NEW, S.NEEDS_EMAIL, S.READY_TO_DRAFT, S.PENDING_APPROVAL,
                                   S.APPROVED, S.SENT, S.UNRESOLVED, S.DISCARDED])
def test_set_email_is_refused_from_every_other_state(repos, manual_founders, state):
    """The manual path never overwrites a verified address or re-routes a
    row that has moved on - refused before the state machine is consulted,
    so the message names the actual state."""
    repos.outreach.get(1).state = str(state)
    repos.outreach.get(1).resolved_email = "verified@acme.com"
    with pytest.raises(ValueError, match="not needs_manual_email"):
        ResolveManualEmail(repos).set_email(1, "typed@acme.com")
    assert repos.outreach.get(1).state == str(state)
    assert repos.outreach.get(1).resolved_email == "verified@acme.com"


def test_set_email_refuses_an_unknown_outreach_id(repos, manual_founders):
    with pytest.raises(ValueError, match="no outreach with id 999"):
        ResolveManualEmail(repos).set_email(999, "x@y.com")


def test_set_email_goes_through_the_state_machine(repos, manual_founders, monkeypatch):
    """Mutation guard: the transition is asserted, not just written."""
    import app.application.resolve_manual_email as module

    def boom(src, dst):
        raise IllegalTransition(f"{src} -> {dst}")

    monkeypatch.setattr(module, "assert_transition", boom)
    with pytest.raises(IllegalTransition):
        ResolveManualEmail(repos).set_email(1, "x@acme.com")
    assert repos.outreach.get(1).state == S.NEEDS_MANUAL_EMAIL


# --- give_up() ---------------------------------------------------------------


def test_give_up_moves_to_unresolved_and_drops_the_row_from_pending(repos, manual_founders):
    use_case = ResolveManualEmail(repos)
    use_case.give_up(2)
    assert repos.outreach.get(2).state == S.UNRESOLVED
    assert [i.outreach.id for i in use_case.pending()] == [1]


def test_give_up_is_refused_from_any_other_state(repos, manual_founders):
    repos.outreach.get(1).state = str(S.READY_TO_DRAFT)
    with pytest.raises(ValueError, match="not needs_manual_email"):
        ResolveManualEmail(repos).give_up(1)
    assert repos.outreach.get(1).state == S.READY_TO_DRAFT


# --- round trip with the drafter ---------------------------------------------


def test_a_manually_resolved_founder_is_drafted_on_the_next_run(repos, manual_founders):
    from app.application.draft_outreach import DraftOutreach
    from tests.fakes import FakeDrafter

    repos.company.get(1).long_description = (
        "Acme builds a payments rail for trucking fleets, settling fuel and toll spend per "
        "vehicle with a ledger reconciled nightly against bank feeds."
    )
    ResolveManualEmail(repos).set_email(1, "priya@acme.com")
    drafter = FakeDrafter([{
        "subject": "Backend intern at Acme",
        "body": "You're settling fuel and toll spend per vehicle at Acme. That ledger is what I keep thinking about.\n\n"
                "I built a scheduler in Python.\n\nWould you take an intern?",
        "research_source_type": "long_description", "research_source_id": 1,
    }])
    result = DraftOutreach(drafter, repos).run()
    assert result.drafted == 1
    assert repos.outreach.get(1).state == S.PENDING_APPROVAL
    assert drafter.calls[0]["founder"]["full_name"] == "Priya Nair"
