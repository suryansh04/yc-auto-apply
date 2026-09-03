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
