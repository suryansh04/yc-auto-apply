from app.application.sync_companies import SyncCompanies
from app.domain.entities import Company
from tests.fakes import FakeYCDirectory

# Deviation from the task-10 brief: the brief's own Step 1 test defines a
# local `FakeRepo.upsert(self, company)` taking one positional `Company`.
# The real `CompanyRepo.upsert` (built in Task 9, after this brief was
# written) is `upsert(**fields)` -- see repositories.py's module docstring.
# `SyncCompanies` must call its repo the same way in tests and in the real
# CLI wiring, so this fake mirrors the real, already-shipped API instead of
# the brief's predates-the-rename version. Also uses tests/fakes.py's
# existing `FakeYCDirectory` instead of a redundant local `FakeDirectory`,
# per the task instructions ("prefer extending these over inventing new
# doubles").


class FakeCompanyRepo:
    def __init__(self):
        self.rows: dict[int, Company] = {}

    def upsert(self, **fields):
        company = Company(**fields)
        self.rows[company.yc_id] = company
        return company


INDIA = {"id": 1, "slug": "swipe", "name": "Swipe", "website": "https://getswipe.in",
         "regions": ["India"], "all_locations": "Hyderabad, India", "status": "Active",
         "team_size": 60, "isHiring": False, "one_liner": "AI accountant",
         "long_description": "x" * 200, "batch": "Summer 2021"}
US = {**INDIA, "id": 2, "slug": "other", "regions": ["United States of America"],
      "all_locations": "Boston, MA, USA"}
TOO_BIG = {**INDIA, "id": 3, "slug": "big", "team_size": 900}


def test_only_target_companies_are_upserted():
    repo = FakeCompanyRepo()
    result = SyncCompanies(FakeYCDirectory([INDIA, US, TOO_BIG]), repo).run()
    assert result.fetched == 3
    assert result.matched == 1
    assert list(repo.rows) == [1]


def test_domain_is_derived_from_website():
    repo = FakeCompanyRepo()
    SyncCompanies(FakeYCDirectory([INDIA]), repo).run()
    assert repo.rows[1].domain == "getswipe.in"


def test_rerun_is_idempotent():
    repo = FakeCompanyRepo()
    d = FakeYCDirectory([INDIA])
    SyncCompanies(d, repo).run()
    SyncCompanies(d, repo).run()
    assert len(repo.rows) == 1
