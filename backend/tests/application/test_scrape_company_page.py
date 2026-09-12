"""Task 11: scrape founders and research material from a company's YC page
(spec §3/§9). `ScrapeCompanyPage` is pure application logic: it depends only
on the `CompanyPageSource` port and a small bundle of repos, both injected
by the caller (D8) — this test never touches a real database or network.

Deviation from the task-11 brief, disclosed: `ScrapeCompanyPage(source,
repos).run(company)` needs the *local* primary key (`company.id`) to set
`founders.company_id` / `job_postings.company_id` / etc., because those are
real foreign keys into `companies.id`, not the YC-assigned `yc_id`. The
domain `Company` dataclass (`app.domain.entities.Company`) has no `id`
field — only the natural key `yc_id` — because Task 2's entities predate
persistence (Task 9) and were never revisited. In production, `company` is
the real ORM row returned by `CompanyRepo.list_by_status(...)`, which does
have `.id`. `application/` may not import `app.adapters.db.models` (D8), so
`ScrapeCompanyPage` only ever accesses `company.id` / `company.slug`
structurally; this test stands a minimal local `FakeCompany` in for that
real row, matching its shape rather than the incomplete domain dataclass.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from app.application.scrape_company_page import ScrapeCompanyPage, ScrapeRepos
from app.domain.states import OutreachState
from tests.fakes import FakeCompanyPageSource


@dataclass
class FakeCompany:
    id: int
    slug: str


@dataclass
class FakeFounderRow:
    id: int
    company_id: int
    yc_user_id: int
    full_name: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    twitter_url: str | None = None
    bio: str | None = None
    scraped_at: object = None   # final fix wave (F12): stamped via the injected Clock port


class FakeFounderRepo:
    def __init__(self):
        self.rows: dict[int, FakeFounderRow] = {}
        self._by_yc_user_id: dict[int, int] = {}
        self._next_id = 1

    def upsert(self, **fields) -> FakeFounderRow:
        existing_id = self._by_yc_user_id.get(fields["yc_user_id"])
        if existing_id is None:
            row = FakeFounderRow(id=self._next_id, **fields)
            self.rows[row.id] = row
            self._by_yc_user_id[fields["yc_user_id"]] = row.id
            self._next_id += 1
        else:
            row = self.rows[existing_id]
            for k, v in fields.items():
                setattr(row, k, v)
        return row

    def get_by_yc_user_id(self, yc_user_id: int) -> FakeFounderRow | None:
        row_id = self._by_yc_user_id.get(yc_user_id)
        return self.rows.get(row_id) if row_id is not None else None


@dataclass
class FakeOutreachRow:
    id: int
    founder_id: int
    state: str = "new"


class FakeOutreachRepo:
    def __init__(self):
        self.rows: dict[int, FakeOutreachRow] = {}
        self._by_founder_id: dict[int, int] = {}
        self._next_id = 1

    def upsert(self, **fields) -> FakeOutreachRow:
        existing_id = self._by_founder_id.get(fields["founder_id"])
        if existing_id is None:
            row = FakeOutreachRow(id=self._next_id, **fields)
            self.rows[row.id] = row
            self._by_founder_id[fields["founder_id"]] = row.id
            self._next_id += 1
        else:
            row = self.rows[existing_id]
            for k, v in fields.items():
                setattr(row, k, v)
        return row

    def get_by_founder_id(self, founder_id: int) -> FakeOutreachRow | None:
        row_id = self._by_founder_id.get(founder_id)
        return self.rows.get(row_id) if row_id is not None else None


class FakeUpsertOnlyRepo:
    """Stand-in for JobPostingRepo / CompanyNewsRepo / CompanyLaunchRepo —
    none of these are keyed by anything ScrapeCompanyPage needs to look up
    ahead of time, so a plain append-on-upsert fake is enough."""

    def __init__(self):
        self.rows: list[dict] = []

    def upsert(self, **fields):
        self.rows.append(fields)
        return fields


class RaisingSource:
    def fetch_page(self, slug: str) -> dict:
        raise ValueError(f"boom: could not fetch {slug}")


class FakeSession:
    """Stand-in for the real SQLAlchemy `Session` all five real repos share.
    Only `rollback()` matters here — the fix-round-1 regression test asserts
    it gets called when a repo raises mid-persist."""

    def __init__(self):
        self.rollback_calls = 0

    def rollback(self) -> None:
        self.rollback_calls += 1


class ConstraintViolatingFounderRepo(FakeFounderRepo):
    """Simulates the real `FounderRepo` raising at `flush()` time — e.g. a
    scraped founder with `yc_user_id=None` hitting the real table's NOT
    NULL/unique constraint on that column. `FakeFounderRepo.upsert` has no
    such check (a plain dict lookup happily keys on `None`), so this
    subclass adds the one the real repo effectively has."""

    def upsert(self, **fields):
        if fields.get("yc_user_id") is None:
            raise RuntimeError("simulated IntegrityError: yc_user_id may not be NULL")
        return super().upsert(**fields)


def make_repos(founder_repo=None, session=None):
    return ScrapeRepos(
        founder=founder_repo or FakeFounderRepo(),
        job_posting=FakeUpsertOnlyRepo(),
        news=FakeUpsertOnlyRepo(),
        launch=FakeUpsertOnlyRepo(),
        outreach=FakeOutreachRepo(),
        session=session or FakeSession(),
    )


PAGE = {
    "founders": [
        {"yc_user_id": 1, "full_name": "Madhav Krishna", "title": "CEO",
         "linkedin_url": "https://linkedin.com/in/madhav", "twitter_url": None,
         "bio": "Founder bio"},
    ],
    "job_postings": [
        {"title": "Backend Intern", "employment_type": "Internship",
         "min_experience": None, "location": "Bengaluru, IN", "url": "/jobs/1"},
    ],
    "news": [
        {"title": "Vahan raises $10M - The Economic Times",
         "url": "https://example.com/a", "source": "The Economic Times",
         "published_at": "May 04, 2025"},
    ],
    "launches": [
        {"title": "Vahan launch", "body": "reach me at [x](mailto:madhav@vahan.ai)",
         "published_email": "madhav@vahan.ai"},
    ],
}


def test_scrapes_founders_postings_news_and_launches():
    repos = make_repos()
    source = FakeCompanyPageSource({"vahan": PAGE})
    company = FakeCompany(id=42, slug="vahan")

    result = ScrapeCompanyPage(source, repos).run(company)

    assert result.error is None
    assert result.founders == 1
    assert result.job_postings == 1
    assert result.news == 1
    assert result.launches == 1
    assert repos.founder.rows[1].company_id == 42
    assert repos.founder.rows[1].full_name == "Madhav Krishna"


def test_new_founder_gets_an_outreach_row_in_new_state():
    repos = make_repos()
    source = FakeCompanyPageSource({"vahan": PAGE})
    company = FakeCompany(id=42, slug="vahan")

    result = ScrapeCompanyPage(source, repos).run(company)

    assert result.new_founders == 1
    founder = repos.founder.rows[1]
    outreach = repos.outreach.get_by_founder_id(founder.id)
    assert outreach is not None
    assert outreach.state == str(OutreachState.NEW)


def test_rerun_does_not_reset_an_advanced_outreach_state():
    repos = make_repos()
    source = FakeCompanyPageSource({"vahan": PAGE})
    company = FakeCompany(id=42, slug="vahan")

    ScrapeCompanyPage(source, repos).run(company)
    founder_id = repos.founder.rows[1].id
    repos.outreach.upsert(founder_id=founder_id, state="ready_to_draft")

    result = ScrapeCompanyPage(source, repos).run(company)

    assert result.new_founders == 0, "founder already existed on the second pass"
    assert repos.outreach.get_by_founder_id(founder_id).state == "ready_to_draft"


def test_employment_type_is_persisted_from_the_payload_type():
    repos = make_repos()
    source = FakeCompanyPageSource({"vahan": PAGE})
    company = FakeCompany(id=42, slug="vahan")

    ScrapeCompanyPage(source, repos).run(company)

    assert repos.job_posting.rows[0]["employment_type"] == "Internship"


def test_published_email_is_persisted_from_the_launch():
    repos = make_repos()
    source = FakeCompanyPageSource({"vahan": PAGE})
    company = FakeCompany(id=42, slug="vahan")

    ScrapeCompanyPage(source, repos).run(company)

    assert repos.launch.rows[0]["published_email"] == "madhav@vahan.ai"


def test_news_published_at_string_is_parsed_into_a_datetime():
    repos = make_repos()
    source = FakeCompanyPageSource({"vahan": PAGE})
    company = FakeCompany(id=42, slug="vahan")

    ScrapeCompanyPage(source, repos).run(company)

    published_at = repos.news.rows[0]["published_at"]
    assert published_at == datetime(2025, 5, 4, tzinfo=timezone.utc)


def test_news_source_is_persisted_from_the_payload():
    repos = make_repos()
    source = FakeCompanyPageSource({"vahan": PAGE})
    company = FakeCompany(id=42, slug="vahan")

    ScrapeCompanyPage(source, repos).run(company)

    assert repos.news.rows[0]["source"] == "The Economic Times"


def test_source_failure_is_recorded_against_the_company_and_does_not_raise():
    repos = make_repos()
    company = FakeCompany(id=42, slug="vahan")

    result = ScrapeCompanyPage(RaisingSource(), repos).run(company)

    assert result.company_slug == "vahan"
    assert result.error is not None
    assert "vahan" in result.error
    assert result.founders == 0
    assert repos.founder.rows == {}


def test_empty_arrays_yield_zero_counts_and_no_error():
    repos = make_repos()
    empty_page = {"founders": [], "job_postings": [], "news": [], "launches": []}
    source = FakeCompanyPageSource({"empty-co": empty_page})
    company = FakeCompany(id=99, slug="empty-co")

    result = ScrapeCompanyPage(source, repos).run(company)

    assert result.error is None
    assert (result.founders, result.job_postings, result.news, result.launches) == (0, 0, 0, 0)


# --- Fix round 1: Critical finding ---
#
# A repo raising during the persist loop (realistic: a scraped founder with
# a null `yc_user_id` hitting the real table's NOT NULL/unique constraint at
# flush time) used to be caught into `result.error` without rolling back the
# session. The CLI reuses one `Session` across the whole batch and commits
# after every `run()`, including a failed one — committing a session
# SQLAlchemy has already marked rollback-required raises
# `PendingRollbackError`, uncaught, which would abort every company *after*
# the failing one with a raw traceback. This is the regression test that
# would have caught it: two companies share one `ScrapeRepos` (and its one
# `FakeSession`), company 1's founder violates the (simulated) constraint,
# and company 2 must still scrape cleanly afterward.
def test_a_flush_time_failure_for_one_company_does_not_abort_the_batch():
    session = FakeSession()
    founder_repo = ConstraintViolatingFounderRepo()
    repos = make_repos(founder_repo=founder_repo, session=session)

    broken_page = {**PAGE, "founders": [{"yc_user_id": None, "full_name": "Ghost Founder"}]}
    source = FakeCompanyPageSource({"broken-co": broken_page, "vahan": PAGE})
    use_case = ScrapeCompanyPage(source, repos)

    company1 = FakeCompany(id=1, slug="broken-co")
    company2 = FakeCompany(id=2, slug="vahan")

    result1 = use_case.run(company1)
    result2 = use_case.run(company2)

    assert result1.error is not None
    assert session.rollback_calls == 1, "run() must roll back after a flush-time failure"

    assert result2.error is None, "company 2 must still be scraped after company 1 failed"
    assert result2.founders == 1
    assert result2.job_postings == 1
    assert result2.news == 1
    assert result2.launches == 1
    assert founder_repo.get_by_yc_user_id(1).company_id == 2


# --- Final fix wave (F12, ruling FR-4): every founder upsert is stamped
# `scraped_at` from the injected Clock port.

def test_founders_are_stamped_scraped_at_from_the_clock():
    from tests.fakes import FrozenClock

    repos = make_repos()
    clock = FrozenClock()
    ScrapeCompanyPage(FakeCompanyPageSource({"vahan": PAGE}), repos, clock=clock).run(
        FakeCompany(id=1, slug="vahan"))
    assert repos.founder.rows[1].scraped_at == clock.now()


def test_scraped_at_defaults_to_a_real_utc_time():
    from datetime import datetime, timezone

    repos = make_repos()
    ScrapeCompanyPage(FakeCompanyPageSource({"vahan": PAGE}), repos).run(FakeCompany(id=1, slug="vahan"))
    stamped = repos.founder.rows[1].scraped_at
    assert isinstance(stamped, datetime) and stamped.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - stamped).total_seconds()) < 60
