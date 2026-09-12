import re
import unicodedata


def _clean(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", ascii_only.lower())


def normalise_name(full_name: str) -> tuple[str, str]:
    tokens = [t for t in (_clean(p) for p in full_name.split()) if t]
    if not tokens:
        return ("", "")
    if len(tokens) == 1:
        return (tokens[0], "")
    return (tokens[0], tokens[-1])


# Live finding (2026-09-13): YC stores some names with a legal suffix
# ("Wifi Dabba, Inc."). Emails should say "Wifi Dabba"; the validator's
# "names the company" check and the drafter prompt both use this form.
_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(?:inc\.?|llc|ltd\.?|limited|pvt\.?\s*ltd\.?|private\s+limited|corp\.?|co\.?)\s*$",
    re.IGNORECASE,
)


def company_display_name(name: str) -> str:
    """The company name as a person would write it mid-sentence: trailing
    legal suffixes (Inc., LLC, Pvt. Ltd., Private Limited, ...) removed."""
    cleaned = (name or "").strip()
    stripped = _LEGAL_SUFFIX.sub("", cleaned).strip(" ,")
    return stripped or cleaned
