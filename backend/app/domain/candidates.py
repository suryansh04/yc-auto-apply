from dataclasses import dataclass

from app.domain.names import normalise_name


@dataclass(frozen=True)
class Candidate:
    address: str
    pattern: str
    priority: int


# Ordered by how common each shape is at startups. Order matters: it is what
# keeps a full run inside the verification budget via early exit (spec §9).
_PATTERNS: list[tuple[str, str]] = [
    ("first", "{first}"),
    ("first.last", "{first}.{last}"),
    ("f_last", "{f}{last}"),
    ("firstlast", "{first}{last}"),
    ("first_last", "{first}_{last}"),
    ("first_l", "{first}{l}"),
    ("last", "{last}"),
]


def generate_candidates(full_name: str, domain: str) -> list[Candidate]:
    first, last = normalise_name(full_name)
    if not first:
        return []
    parts = {"first": first, "last": last, "f": first[0], "l": last[0] if last else ""}

    out: list[Candidate] = []
    seen: set[str] = set()
    for name, template in _PATTERNS:
        # Skip any template that references a name part which is empty for
        # this name (mononym: last and its initial l are both ""). Checking
        # both placeholders explicitly avoids relying on _PATTERNS' order to
        # have `first` produce the same string first and get deduped away.
        if not last and ("{last}" in template or "{l}" in template):
            continue
        local = template.format(**parts)
        address = f"{local}@{domain}"
        if address in seen:
            continue
        seen.add(address)
        out.append(Candidate(address=address, pattern=name, priority=len(out) + 1))
    return out
