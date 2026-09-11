"""Task 10: sync the target company list from the yc-oss dataset (spec §3/§9).

`SyncCompanies` is pure application logic: it depends only on the
`YCDirectory` port and a company repo, both injected by the caller (D8).
Every row the directory returns is filtered through `is_target_company`
(D25) and the survivors are upserted. Rerunning is idempotent because the
repo's `upsert` is keyed on the natural key `yc_id`, not an insert.
"""

from dataclasses import dataclass
from urllib.parse import urlparse

from app.domain.targeting import is_target_company


@dataclass
class SyncResult:
    fetched: int
    matched: int
    upserted: int


def derive_domain(website: str | None) -> str | None:
    if not website:
        return None
    netloc = urlparse(website if "//" in website else f"//{website}").netloc
    return netloc.removeprefix("www.").lower() or None


class SyncCompanies:
    def __init__(self, directory, repo):
        self._directory, self._repo = directory, repo

    def run(self) -> SyncResult:
        rows = self._directory.fetch_companies()
        matched = [r for r in rows if is_target_company(r)]
        for raw in matched:
            self._repo.upsert(
                yc_id=raw["id"], slug=raw["slug"], name=raw["name"],
                website=raw.get("website"), domain=derive_domain(raw.get("website")),
                batch=raw.get("batch"), team_size=raw.get("team_size"),
                status=raw.get("status"), is_hiring=bool(raw.get("isHiring")),
                one_liner=raw.get("one_liner"),
                long_description=raw.get("long_description"),
            )
        return SyncResult(len(rows), len(matched), len(matched))
