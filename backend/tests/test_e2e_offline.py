"""Task 16: the whole Phase 1 pipeline, offline, on the fakes.

sync -> scrape -> resolve -> draft -> review (approve) -> send, exactly the
command chain `app.cli` runs, with every port replaced by its fake
(`tests/fakes.py`) and every repo by the in-memory `FakeRepos` bundle. No
database, no network, no env vars. Three founders go in: two resolve, are
drafted, approved and sent; one resolves to nothing usable and must be left
in `NEEDS_MANUAL_EMAIL` with no draft, no approval and no send.

The final assertion is ruling R2 end to end: what the sender got is
`compose_wire_text` of the stored body - greeting, body, signature, opt-out
- and the stored body itself is untouched.
"""

from app.application.draft_outreach import DraftOutreach
from app.application.resolve_emails import ResolveEmails
from app.application.review_outreach import ReviewOutreach
from app.application.scrape_company_page import ScrapeCompanyPage, ScrapeRepos
from app.application.send_approved import SendApproved
from app.application.sync_companies import SyncCompanies
from app.domain.compose import OPT_OUT_LINE, compose_wire_text, first_name
from app.domain.states import OutreachState as S
from tests.application.conftest import VAHAN_DESCRIPTION, VAHAN_NEWS
from tests.fakes import (
    FakeCompanyPageSource,
    FakeDrafter,
    FakeSender,
    FakeVerifier,
    FakeYCDirectory,
    FrozenClock,
)

ACME_DESCRIPTION = (
    "Acme builds a payments rail for trucking fleets, settling fuel and toll spend per "
    "vehicle with a ledger reconciled nightly against bank feeds."
)
ACME_NEWS = "Acme raises seed round to build a payments rail for trucking fleets"

DIRECTORY = [
    {"id": 1, "slug": "vahan", "name": "Vahan", "website": "https://www.vahan.ai",
     "batch": "W22", "team_size": 40, "status": "Active", "isHiring": True,
     "regions": ["India"], "all_locations": "Bengaluru, India",
     "one_liner": "Recruitment over voice", "long_description": VAHAN_DESCRIPTION},
    {"id": 2, "slug": "acme", "name": "Acme", "website": "acme.com",
     "batch": "S23", "team_size": 12, "status": "Active", "isHiring": False,
     "regions": ["India"], "all_locations": "Mumbai, India",
     "one_liner": "Fuel cards for truckers", "long_description": ACME_DESCRIPTION},
    {"id": 3, "slug": "zeta", "name": "Zeta", "website": "https://zeta.io",
     "batch": "S23", "team_size": 3, "status": "Active", "isHiring": False,
     "regions": ["India"], "all_locations": "Pune, India",
     "one_liner": "Ledgers for kiranas", "long_description": "Short."},
    {"id": 4, "slug": "usaco", "name": "US Co", "website": "https://usaco.com",
     "batch": "S23", "team_size": 12, "status": "Active", "isHiring": True,
     "regions": ["United States"], "all_locations": "San Francisco, CA",
     "one_liner": "Not a target", "long_description": "Not in India."},
]

PAGES = {
    "vahan": {
        "founders": [{"yc_user_id": 101, "full_name": "Madhav Krishna", "title": "Founder"}],
        "news": [{"title": VAHAN_NEWS, "url": "https://news.example.com/vahan",
                  "source": "The Economic Times", "published_at": "May 04, 2025"}],
    },
    "acme": {
        "founders": [{"yc_user_id": 102, "full_name": "Asha Rao", "title": "CEO"}],
        "news": [{"title": ACME_NEWS, "url": "https://news.example.com/acme",
                  "source": "TechCrunch", "published_at": "Jun 01, 2025"}],
        "job_postings": [{"title": "Senior Engineer", "employment_type": "Full-time",
                          "min_experience": "6+ years", "url": "https://acme.com/jobs/1"}],
    },
    "zeta": {
        "founders": [{"yc_user_id": 103, "full_name": "Vikram Shah", "title": "Founder"}],
    },
}

VERDICTS = {"madhav@vahan.ai": "valid", "asha@acme.com": "valid"}  # every zeta.io guess: invalid

VAHAN_DRAFT = {
    "subject": "Backend intern at Vahan",
    "body": ("You're matching blue-collar workers to jobs over voice at Vahan. "
             "That routing problem is what I keep thinking about.\n\n"
             "I built a scheduler in Python.\n\nWould you take an intern?"),
    "research_source_type": "news",
}
ACME_DRAFT = {
    "subject": "Backend intern at Acme",
    "body": ("You're building a payments rail for trucking fleets at Acme. "
             "That reconciliation problem is what I keep thinking about.\n\n"
             "I built a scheduler in Python.\n\nWould you take an intern?"),
    "research_source_type": "news",
}

SIGNATURE = "Suryansh Pratap Singh\nB.Tech CSE, 2027"
NOW = FrozenClock()


def _founder(repos, name):
    return next(f for f in repos.founder.rows.values() if f.full_name == name)


def test_phase_1_pipeline_end_to_end_offline(repos):
    # 1. sync ------------------------------------------------------------
    sync = SyncCompanies(FakeYCDirectory(DIRECTORY), repos.company).run()
    assert (sync.fetched, sync.matched, sync.upserted) == (4, 3, 3)
    companies = repos.company.list_by_status("Active")
    assert sorted(c.slug for c in companies) == ["acme", "vahan", "zeta"]
    assert {c.slug: c.domain for c in companies} == {"vahan": "vahan.ai", "acme": "acme.com", "zeta": "zeta.io"}

    # 2. scrape (one run per company, as `cli.scrape` does) ---------------
    scrape = ScrapeCompanyPage(FakeCompanyPageSource(PAGES), ScrapeRepos(
        founder=repos.founder, job_posting=repos.job_posting, news=repos.news,
        launch=repos.launch, outreach=repos.outreach, session=repos.session,
    ))
    results = [scrape.run(c) for c in companies]
    assert all(r.error is None for r in results)
    assert sum(r.new_founders for r in results) == 3
    assert {o.state for o in repos.outreach.all()} == {S.NEW}

    # 3. resolve -----------------------------------------------------------
    verifier = FakeVerifier(VERDICTS, quota=100)
    resolve = ResolveEmails(verifier, repos, clock=NOW).run()
    assert (resolve.resolved, resolve.manual, resolve.paused_on_quota) == (2, 1, False)
    madhav, asha, vikram = (_founder(repos, n) for n in ("Madhav Krishna", "Asha Rao", "Vikram Shah"))
    assert repos.outreach.get_by_founder_id(madhav.id).resolved_email == "madhav@vahan.ai"
    assert repos.outreach.get_by_founder_id(asha.id).resolved_email == "asha@acme.com"
    assert repos.outreach.get_by_founder_id(vikram.id).state == S.NEEDS_MANUAL_EMAIL
    assert repos.outreach.get_by_founder_id(vikram.id).resolved_email is None
    assert "madhav.krishna@vahan.ai" not in verifier.calls          # stopped at the first valid

    # 4. draft (the `repos` fixture already holds the parsed resume) --------
    vahan_news = repos.news.list_by_company_id(madhav.company_id)[0].id
    acme_news = repos.news.list_by_company_id(asha.company_id)[0].id
    drafter = FakeDrafter([{**VAHAN_DRAFT, "research_source_id": vahan_news},
                           {**ACME_DRAFT, "research_source_id": acme_news}])
    draft = DraftOutreach(drafter, repos).run()
    assert (draft.drafted, draft.manual_write, draft.retried, draft.errors) == (2, 0, 0, 0)
    assert len(drafter.calls) == 2
    # D23: the senior posting was never offered to the drafter.
    assert all(s["kind"] != "job_posting" for call in drafter.calls for s in call["available_sources"])
    for f in (madhav, asha):
        assert repos.outreach.get_by_founder_id(f.id).state == S.PENDING_APPROVAL
        assert repos.message.get_outbound(repos.outreach.get_by_founder_id(f.id).id, 0).approved_at is None

    # 5. review: approve both -----------------------------------------------
    review = ReviewOutreach(repos)
    items = review.pending()
    assert {i.founder.full_name for i in items} == {"Madhav Krishna", "Asha Rao"}
    for item in items:
        review.approve(item.outreach.id, NOW.now())
    assert review.pending() == []
    for f in (madhav, asha):
        outreach = repos.outreach.get_by_founder_id(f.id)
        assert outreach.state == S.APPROVED
        assert repos.message.get_outbound(outreach.id, 0).approved_at == NOW.now()

    # 6. send ------------------------------------------------------------------
    sender = FakeSender()
    sent = SendApproved(sender, NOW, repos, signature=SIGNATURE, attachment_path="resume.pdf").run(cap=10)
    assert (sent.sent, sent.errors, sent.skipped_cap, sent.needs_manual_check) == (2, 0, 0, 0)
    assert [m["to"] for m in sender.sent] == ["madhav@vahan.ai", "asha@acme.com"]

    # -- final state -----------------------------------------------------------
    for f in (madhav, asha):
        outreach = repos.outreach.get_by_founder_id(f.id)
        assert outreach.state == S.SENT
        row = repos.message.get_outbound(outreach.id, 0)
        assert row.gmail_message_id and row.gmail_thread_id and row.sent_at == NOW.now()
        assert outreach.next_action_at is not None

    # The NEEDS_MANUAL_EMAIL founder was never drafted, approved or sent.
    vikram_outreach = repos.outreach.get_by_founder_id(vikram.id)
    assert vikram_outreach.state == S.NEEDS_MANUAL_EMAIL
    assert repos.message.list_by_outreach_id(vikram_outreach.id) == []
    assert all(m["to"] != "vikram@zeta.io" for m in sender.sent)

    # Ruling R2 end to end: the wire text is composed from the stored body,
    # which itself is untouched.
    madhav_row = repos.message.get_outbound(repos.outreach.get_by_founder_id(madhav.id).id, 0)
    assert madhav_row.body == VAHAN_DRAFT["body"]
    assert sender.sent[0]["body"] == compose_wire_text(first_name(madhav.full_name), madhav_row.body, SIGNATURE)
    assert sender.sent[0]["body"].startswith("Hi Madhav,\n\n")
    assert sender.sent[0]["body"].endswith(f"{SIGNATURE}\n\n{OPT_OUT_LINE}")
    assert sender.sent[0]["subject"] == "Backend intern at Vahan"


def test_the_send_step_refuses_a_draft_that_skipped_review(repos):
    """Approval cannot be bypassed (spec §13): running the chain without
    the review step sends nothing."""
    SyncCompanies(FakeYCDirectory(DIRECTORY), repos.company).run()
    scrape = ScrapeCompanyPage(FakeCompanyPageSource(PAGES), ScrapeRepos(
        founder=repos.founder, job_posting=repos.job_posting, news=repos.news,
        launch=repos.launch, outreach=repos.outreach, session=repos.session,
    ))
    for c in repos.company.list_by_status("Active"):
        scrape.run(c)
    ResolveEmails(FakeVerifier(VERDICTS, quota=100), repos, clock=NOW).run()
    madhav, asha = _founder(repos, "Madhav Krishna"), _founder(repos, "Asha Rao")
    DraftOutreach(FakeDrafter([
        {**VAHAN_DRAFT, "research_source_id": repos.news.list_by_company_id(madhav.company_id)[0].id},
        {**ACME_DRAFT, "research_source_id": repos.news.list_by_company_id(asha.company_id)[0].id},
    ]), repos).run()

    sender = FakeSender()
    result = SendApproved(sender, NOW, repos, signature=SIGNATURE).run(cap=10)
    assert sender.sent == [] and result.sent == 0
    assert {repos.outreach.get_by_founder_id(f.id).state for f in (madhav, asha)} == {S.PENDING_APPROVAL}
