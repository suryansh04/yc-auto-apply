"""Company targeting filter. See spec D25: band is 1-200, not 11-200."""

MIN_TEAM_SIZE = 1
MAX_TEAM_SIZE = 200


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
