"""Root fixtures (final fix wave, F9 / ruling FR-5).

EVERY fixture built on the in-memory `FakeRepos` bundle lives here, built
from the plain fake classes in `tests/fakes_repos.py`; there is no
`tests/application/conftest.py` any more. Ruling FR-5 asked for the shared
fixtures to move here and the application-only ones to stay in a package
conftest, but pytest 9.1.1 drops a package conftest's fixtures for the
second interleaved argument even when no conftest imports another
(`pytest tests/application/test_resolve_emails.py tests/test_cli.py
tests/application/test_draft_outreach.py` -> `fixture 'ready_founder' not
found`, reproduced with the split in place), so the only layout that makes
an interleaved invocation resolve every fixture is a single root conftest.

The brief's `test_cli_review.py` names `a_pending_message` /
`a_news_source`; they are built from the same fake row classes, so the
review screen is rendered from exactly the shapes the use case hands the
CLI.
"""

import copy

import pytest

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _ignore_operator_dotenv(monkeypatch):
    """The suite must behave identically whether or not the operator has a
    real `backend/.env` (live run, 2026-09-13: LLM_PROVIDER=sarvam in the
    real file flipped the Gemini free-tier CLI tests). Disable the env file
    for every test; tests set what they need via monkeypatch.setenv."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
from datetime import datetime


from app.domain.states import OutreachState
from tests.fakes_repos import (
    MANUAL_WRITE_ERROR,
    VAHAN_DESCRIPTION,
    PENDING_BODY,
    PENDING_SUBJECT,
    RESUME_CLAIMS,
    VAHAN_NEWS,
    FakeCompanyRepo,
    FakeEmailCandidateRepo,
    FakeFounderRepo,
    FakeFounderRow,
    FakeJobPostingRepo,
    FakeJobRepo,
    FakeLaunchRepo,
    FakeMessageRepo,
    FakeMessageRow,
    FakeNewsRepo,
    FakeNewsRow,
    FakeOutreachRepo,
    FakeRepos,
    FakeResumeProfileRepo,
    FakeSession,
    _add_founder_with_draft,
    _add_pending_founder,
)


@pytest.fixture
def repos() -> FakeRepos:
    outreach = FakeOutreachRepo()
    bundle = FakeRepos(
        founder=FakeFounderRepo(outreach),
        company=FakeCompanyRepo(),
        launch=FakeLaunchRepo(),
        outreach=outreach,
        email_candidate=FakeEmailCandidateRepo(),
        news=FakeNewsRepo(),
        job_posting=FakeJobPostingRepo(),
        message=FakeMessageRepo(),
        resume_profile=FakeResumeProfileRepo(),
        job=FakeJobRepo(),
        session=FakeSession(),
    )
    # deepcopy (Task 16 fix round 1): a test that flips a claim's `span_verified`
    # in place must not leak into the module-level RESUME_CLAIMS.
    bundle.resume_profile.save("Built a scheduler in Python, cut wait 50%", copy.deepcopy(RESUME_CLAIMS))
    return bundle



# --- Task 12 fixtures ---------------------------------------------------------


@pytest.fixture
def a_founder(repos: FakeRepos) -> FakeFounderRow:
    """One founder, one-founder company, `NEEDS_EMAIL`. Named/domained to
    match the brief's own test bodies: `generate_candidates("Pratish
    Gandhi", "onefin.com")` yields `pratish@onefin.com` before
    `pratish.gandhi@onefin.com`."""
    repos.company.add(id=1, domain="onefin.com")
    founder = repos.founder.add(
        id=1, company_id=1, full_name="Pratish Gandhi",
        linkedin_url="https://linkedin.com/in/pratish-gandhi",
    )
    repos.outreach.add(id=1, founder_id=founder.id, state=str(OutreachState.NEEDS_EMAIL))
    return founder


@pytest.fixture
def founder_with_launch_email(repos: FakeRepos) -> FakeFounderRow:
    """A single-founder company whose launch post carries a
    `published_email` — the D18 short-circuit applies unconditionally here
    since there is exactly one founder to attribute it to."""
    repos.company.add(id=2, domain="acme.com")
    founder = repos.founder.add(id=2, company_id=2, full_name="Jane Founder")
    repos.outreach.add(id=2, founder_id=founder.id, state=str(OutreachState.NEEDS_EMAIL))
    repos.launch.add(company_id=2, body="Reach me at jane@acme.com", published_email="jane@acme.com")
    return founder


@pytest.fixture
def two_founders(repos: FakeRepos) -> list[FakeFounderRow]:
    """Two unrelated single-founder companies, both `NEEDS_EMAIL`, no
    launches — used to exercise a quota that runs out partway through a
    batch run."""
    repos.company.add(id=3, domain="one.com")
    f1 = repos.founder.add(id=3, company_id=3, full_name="Asha Rao")
    repos.outreach.add(id=3, founder_id=f1.id, state=str(OutreachState.NEEDS_EMAIL))

    repos.company.add(id=4, domain="two.com")
    f2 = repos.founder.add(id=4, company_id=4, full_name="Vikram Shah")
    repos.outreach.add(id=4, founder_id=f2.id, state=str(OutreachState.NEEDS_EMAIL))

    return [f1, f2]



# --- Task 14 fixtures ---------------------------------------------------------


@pytest.fixture
def ready_founder(repos: FakeRepos) -> FakeFounderRow:
    """Madhav at Vahan, `READY_TO_DRAFT`, with one news item (id=1 — the id
    the task-14 brief's `GOOD` draft cites) and a long description over the
    80-character floor. No launch, no postings, so the default source list
    is exactly `[news, long_description]`."""
    repos.company.add(id=5, domain="vahan.ai", name="Vahan",
                      one_liner="Recruitment over voice", long_description=VAHAN_DESCRIPTION)
    founder = repos.founder.add(id=5, company_id=5, full_name="Madhav Krishna", title="Founder")
    repos.outreach.add(id=5, founder_id=founder.id, state=str(OutreachState.READY_TO_DRAFT))
    repos.news.add(id=1, company_id=5, title=VAHAN_NEWS, source="The Economic Times")
    return founder


@pytest.fixture
def founder_with_senior_posting(repos: FakeRepos) -> FakeFounderRow:
    """A `READY_TO_DRAFT` founder whose company has no news and no launch,
    only a senior (6+ years) posting and a long description — the D23 case:
    the posting must never be offered, not even as the only alternative to
    the description."""
    repos.company.add(
        id=6, domain="acme.com", name="Acme", one_liner="Payments for fleets",
        long_description=("Acme builds a payments rail for trucking fleets, settling fuel and toll "
                          "spend per vehicle with a ledger reconciled nightly against bank feeds."),
    )
    founder = repos.founder.add(id=6, company_id=6, full_name="Riya Nair")
    repos.outreach.add(id=6, founder_id=founder.id, state=str(OutreachState.READY_TO_DRAFT))
    repos.job_posting.add(id=1, company_id=6, title="Senior Engineer",
                          employment_type="Full-time", min_experience="6+ years")
    return founder



# --- Task 15 fixtures ---------------------------------------------------------


@pytest.fixture
def one_approved(repos: FakeRepos) -> FakeFounderRow:
    """Madhav, `APPROVED`, draft approved, address resolved."""
    return _add_founder_with_draft(repos, 10, "Madhav Krishna", "madhav@example.com",
                                   OutreachState.APPROVED, approved=True)


@pytest.fixture
def pending_and_approved(repos: FakeRepos) -> tuple[FakeFounderRow, FakeFounderRow]:
    """One `PENDING_APPROVAL` founder (draft not approved) and one
    `APPROVED` founder at `approved@example.com` — the brief's own address."""
    pending = _add_founder_with_draft(repos, 11, "Riya Nair", "pending@example.com",
                                      OutreachState.PENDING_APPROVAL, approved=False)
    approved = _add_founder_with_draft(repos, 12, "Asha Rao", "approved@example.com",
                                       OutreachState.APPROVED, approved=True)
    return pending, approved


@pytest.fixture
def five_approved(repos: FakeRepos) -> list[FakeFounderRow]:
    names = ["Asha Rao", "Vikram Shah", "Riya Nair", "Pratish Gandhi", "Jane Founder"]
    return [
        _add_founder_with_draft(repos, 20 + i, name, f"founder{i}@example.com",
                                OutreachState.APPROVED, approved=True)
        for i, name in enumerate(names)
    ]


@pytest.fixture
def sent_earlier(repos: FakeRepos):
    """`sent_earlier(n, at=...)` adds `n` already-sent outbound rows (with
    Gmail ids) for `SENT` founders, stamped `at` — for the rolling-window
    cap tests (ruling R-A)."""
    def _add(n: int, at: datetime) -> None:
        for i in range(n):
            id = 100 + len(repos.message.rows)
            _add_founder_with_draft(repos, id, f"Sent Earlier{i}", f"earlier{id}@example.com",
                                    OutreachState.SENT, approved=True)
            row = repos.message.get_outbound(id, 0)
            row.sent_at = at
            row.gmail_message_id = f"earlier-{id}"
            row.gmail_thread_id = f"earlier-thread-{id}"
    return _add


# --- Task 16 fixtures ---------------------------------------------------------


@pytest.fixture
def one_pending(repos: FakeRepos) -> FakeFounderRow:
    """Madhav, `PENDING_APPROVAL`, a validating draft, no `last_error`."""
    return _add_pending_founder(repos, 30, "Madhav Krishna", "madhav@example.com")


@pytest.fixture
def two_pending(repos: FakeRepos) -> tuple[FakeFounderRow, FakeFounderRow]:
    return (_add_pending_founder(repos, 31, "Madhav Krishna", "madhav@example.com"),
            _add_pending_founder(repos, 32, "Asha Rao", "asha@example.com"))


@pytest.fixture
def manual_write_pending(repos: FakeRepos) -> FakeFounderRow:
    """Riya: the drafter failed validation twice, so the rejected draft is
    stored and `last_error` carries the reasons (needs manual write)."""
    return _add_pending_founder(
        repos, 33, "Riya Nair", "riya@example.com",
        body="I hope this email finds you well. Would you take an intern?",
        last_error=MANUAL_WRITE_ERROR,
    )


@pytest.fixture
def no_material_pending(repos: FakeRepos) -> FakeFounderRow:
    """Vikram: no research material at all, so `DraftOutreach` never
    called the drafter and wrote NO message row - only the flag."""
    founder = _add_pending_founder(
        repos, 34, "Vikram Shah", "vikram@example.com", with_row=False,
        last_error="needs manual write: no research material",
    )
    repos.news.rows = [n for n in repos.news.rows if n.company_id != 34]
    repos.company.get(34).long_description = "Too short."
    return founder


@pytest.fixture
def a_news_source() -> FakeNewsRow:
    return FakeNewsRow(id=1, company_id=5, title=VAHAN_NEWS, source="The Economic Times")


@pytest.fixture
def a_pending_message() -> FakeMessageRow:
    return FakeMessageRow(
        id=1, outreach_id=5, sequence_step=0, direction="outbound",
        subject=PENDING_SUBJECT, body=PENDING_BODY,
        research_source_type="news", research_source_id=1,
    )
