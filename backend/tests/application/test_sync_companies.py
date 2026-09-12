from app.application.sync_companies import SyncCompanies, derive_domain
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
        """Keyed on `yc_id` like the real repo; a partial upsert (final fix
        wave F13: `yc_id` + `status`) merges into the existing row."""
        existing = self.rows.get(fields["yc_id"])
        if existing is None:
            existing = Company(**fields)
            self.rows[existing.yc_id] = existing
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        return existing

    def list_by_status(self, status: str) -> list[Company]:
        return [c for c in self.rows.values() if c.status == status]


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


# Fix round 1 (minor): `derive_domain` used to strip "www." *before*
# lowercasing, so an upper/mixed-case host's "WWW." prefix (a case-sensitive
# exact match, since `str.removeprefix` doesn't fold case) survived the
# strip and only got lowercased afterward, yielding "www.getswipe.in"
# instead of "getswipe.in". Lowercasing first fixes it.
def test_domain_strips_www_regardless_of_its_case():
    assert derive_domain("https://WWW.getswipe.in") == "getswipe.in"
    assert derive_domain("https://getswipe.in") == "getswipe.in"


# --- Final fix wave: F12 (ruling FR-4) `last_synced_at`; F13 (ruling FR-9)
# companies that leave the target band are marked `out_of_band`, and
# restored when they match again.

from app.application.sync_companies import OUT_OF_BAND  # noqa: E402
from tests.fakes import FrozenClock  # noqa: E402


def test_upserted_rows_are_stamped_last_synced_at_from_the_clock():
    repo = FakeCompanyRepo()
    clock = FrozenClock()
    SyncCompanies(FakeYCDirectory([INDIA]), repo, clock=clock).run()
    assert repo.rows[1].last_synced_at == clock.now()


def test_last_synced_at_defaults_to_a_real_utc_time():
    from datetime import datetime, timezone

    repo = FakeCompanyRepo()
    SyncCompanies(FakeYCDirectory([INDIA]), repo).run()
    stamped = repo.rows[1].last_synced_at
    assert isinstance(stamped, datetime) and stamped.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - stamped).total_seconds()) < 60


def test_a_company_that_leaves_the_band_is_marked_out_of_band():
    repo = FakeCompanyRepo()
    SyncCompanies(FakeYCDirectory([INDIA]), repo).run()
    assert repo.rows[1].status == "Active"

    grown = {**INDIA, "team_size": 900}
    result = SyncCompanies(FakeYCDirectory([grown]), repo).run()

    assert result.matched == 0 and result.out_of_band == 1
    assert repo.rows[1].status == OUT_OF_BAND
    assert repo.list_by_status("Active") == []


def test_a_company_that_vanishes_from_the_dataset_is_marked_out_of_band():
    repo = FakeCompanyRepo()
    SyncCompanies(FakeYCDirectory([INDIA]), repo).run()
    clock = FrozenClock()
    result = SyncCompanies(FakeYCDirectory([]), repo, clock=clock).run()
    assert result.out_of_band == 1
    assert repo.rows[1].status == OUT_OF_BAND
    assert repo.rows[1].last_synced_at == clock.now()   # the demotion is a sync too


def test_an_acquired_company_is_marked_out_of_band_not_left_active():
    repo = FakeCompanyRepo()
    SyncCompanies(FakeYCDirectory([INDIA]), repo).run()
    SyncCompanies(FakeYCDirectory([{**INDIA, "status": "Acquired"}]), repo).run()
    assert repo.rows[1].status == OUT_OF_BAND


def test_a_company_that_matches_again_is_restored_to_active():
    repo = FakeCompanyRepo()
    SyncCompanies(FakeYCDirectory([INDIA]), repo).run()
    SyncCompanies(FakeYCDirectory([{**INDIA, "team_size": 900}]), repo).run()
    assert repo.rows[1].status == OUT_OF_BAND

    result = SyncCompanies(FakeYCDirectory([INDIA]), repo).run()

    assert result.matched == 1 and result.out_of_band == 0
    assert repo.rows[1].status == "Active"
    assert [c.yc_id for c in repo.list_by_status("Active")] == [1]


def test_companies_still_in_band_are_untouched_when_another_drops_out():
    other = {**INDIA, "id": 7, "slug": "other-in", "name": "Other"}
    repo = FakeCompanyRepo()
    SyncCompanies(FakeYCDirectory([INDIA, other]), repo).run()
    result = SyncCompanies(FakeYCDirectory([other]), repo).run()
    assert result.out_of_band == 1
    assert repo.rows[1].status == OUT_OF_BAND and repo.rows[7].status == "Active"
