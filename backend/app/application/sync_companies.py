"""Task 10: sync the target company list from the yc-oss dataset (spec §3/§9).

`SyncCompanies` is pure application logic: it depends only on the
`YCDirectory` port and a company repo, both injected by the caller (D8).
Every row the directory returns is filtered through `is_target_company`
(D25) and the survivors are upserted. Rerunning is idempotent because the
repo's `upsert` is keyed on the natural key `yc_id`, not an insert.

Final fix wave:
- **F12 (ruling FR-4):** every upserted row is stamped `last_synced_at`
  from the injected `Clock` port (default: real UTC time).
- **F13 (ruling FR-9):** a company already in the table that no longer
  satisfies `is_target_company` on this sync (acquired, inactive, grown
  past 200, moved out of India, or gone from the dataset) used to keep its
  old `status='Active'` row forever and was still scraped and drafted.
  `run` now marks such rows `status=OUT_OF_BAND` after the upserts;
  `scrape` and `draft` only ever list `Active` companies. A company that
  matches again on a later sync is restored by the ordinary upsert, which
  writes the dataset's own status back (`Active`, since `is_target_company`
  requires it).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.domain.targeting import OUT_OF_BAND_STATUS as OUT_OF_BAND, is_target_company

ACTIVE = "Active"


class _UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class SyncResult:
    fetched: int
    matched: int
    upserted: int
    out_of_band: int = 0   # F13: previously-active rows that stopped matching this sync


def derive_domain(website: str | None) -> str | None:
    if not website:
        return None
    netloc = urlparse(website if "//" in website else f"//{website}").netloc
    # Fix round 1 (minor): lowercase *then* strip "www." — the reverse
    # order missed an upper/mixed-case host, since `str.removeprefix` is a
    # case-sensitive exact match. "https://WWW.getswipe.in" used to survive
    # as "www.getswipe.in" (the prefix strip ran first, saw "WWW." and left
    # it, then lowercasing only fixed the case, not the leftover prefix).
    return netloc.lower().removeprefix("www.") or None


class SyncCompanies:
    def __init__(self, directory, repo, clock=None):
        self._directory, self._repo = directory, repo
        self._clock = clock or _UTCClock()

    def run(self) -> SyncResult:
        rows = self._directory.fetch_companies()
        matched = [r for r in rows if is_target_company(r)]
        now = self._clock.now()
        for raw in matched:
            self._repo.upsert(
                yc_id=raw["id"], slug=raw["slug"], name=raw["name"],
                website=raw.get("website"), domain=derive_domain(raw.get("website")),
                batch=raw.get("batch"), team_size=raw.get("team_size"),
                status=raw.get("status"), is_hiring=bool(raw.get("isHiring")),
                one_liner=raw.get("one_liner"),
                long_description=raw.get("long_description"),
                last_synced_at=now,
            )
        matched_ids = {raw["id"] for raw in matched}
        dropped = 0
        for company in self._repo.list_by_status(status=ACTIVE):
            if company.yc_id not in matched_ids:
                self._repo.upsert(yc_id=company.yc_id, status=OUT_OF_BAND, last_synced_at=now)
                dropped += 1
        return SyncResult(len(rows), len(matched), len(matched), out_of_band=dropped)
