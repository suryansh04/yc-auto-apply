"""Company targeting filter. See spec D25: band is 1-200, not 11-200."""

MIN_TEAM_SIZE = 1
MAX_TEAM_SIZE = 200

# Final fix wave (F13, ruling FR-9): the `companies.status` value
# `SyncCompanies` writes on a row that stopped satisfying `is_target_company`
# (YC's own values are "Active", "Acquired", "Inactive", "Public"). `scrape`
# lists only "Active"; `DraftOutreach` skips founders at such a company.
OUT_OF_BAND_STATUS = "out_of_band"


def is_target_company(raw: dict) -> bool:
    if (raw.get("status") or "").lower() != "active":
        return False
    team_size = raw.get("team_size")
    if not isinstance(team_size, int) or isinstance(team_size, bool):
        return False
    if not MIN_TEAM_SIZE <= team_size <= MAX_TEAM_SIZE:
        return False
    haystack = " ".join(raw.get("regions") or []) + " " + (raw.get("all_locations") or "")
    return "india" in haystack.lower()
