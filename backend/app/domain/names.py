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
    # "Statiq." is stored with its trailing period (live, 2026-09-16); a
    # person writes "Statiq" mid-sentence.
    stripped = _LEGAL_SUFFIX.sub("", cleaned).strip(" ,.")
    return stripped or cleaned


# Live finding (2026-09-13): the "names the company" rule compared against the
# whole stored name, so a good Cashfree email saying "Cashfree" was rejected
# because YC stores "Cashfree Payments". A reader would say it names the
# company. Accept the distinctive leading part too — but only when it is
# distinctive: a generic word like "The", "Payments" or "Better" must not
# stand in for the company on its own.
_GENERIC_NAME_WORDS = {
    "the", "a", "an", "and", "payments", "technologies", "technology", "labs",
    "systems", "solutions", "services", "software", "digital", "group",
    "global", "india", "better", "health", "care", "app", "ai", "data",
    "cloud", "works", "studio", "studios", "company", "ventures", "capital",
    "network", "networks", "media", "tech",
}
_MIN_DISTINCTIVE_LEN = 4


def company_name_variants(name: str) -> list[str]:
    """Ways a person might write this company mid-sentence, longest first:
    the display name, then its distinctive leading word if there is one."""
    display = company_display_name(name)
    if not display:
        return []
    variants = [display]
    words = display.split()
    if len(words) > 1:
        head = words[0].strip(",.&")
        if len(head) >= _MIN_DISTINCTIVE_LEN and head.lower() not in _GENERIC_NAME_WORDS:
            variants.append(head)
    return variants
