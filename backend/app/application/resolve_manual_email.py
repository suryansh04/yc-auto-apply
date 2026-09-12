"""Final fix wave (F3, ruling FR-1): the operator's way out of
`NEEDS_MANUAL_EMAIL`.

Spec §9 sends a founder here when every generated candidate came back
invalid/unknown, when the domain is catch-all (never guess), when the
company has no domain at all, or when a launch's `published_email` could
not be attributed to this founder. The spec assigns the pick/type action
to the Phase 2 dashboard; until then this use case is what `list-manual`
and `set-email` drive, so roughly a third of the run is reachable without
raw SQL.

`ResolveManualEmail` is pure application logic over a bundle of repos
(`ManualEmailRepos`); it never imports `app.adapters` and never commits.

- **`pending()`** lists every `NEEDS_MANUAL_EMAIL` founder with what a
  human needs to pick or find an address: the company (name, domain), the
  `email_candidates` rows already judged (address + verdict, in the
  priority order they were tried), the founder's LinkedIn URL, and the
  published-email *hint* if `ResolveEmails` persisted one (a candidate
  row with verdict `published` — a launch address it could not attribute
  to this founder). Ordered by company name, then founder name.
- **`set_email(outreach_id, address)`** stores the typed/picked address on
  the outreach row and moves it `NEEDS_MANUAL_EMAIL -> READY_TO_DRAFT`
  through `assert_transition`. The address must look like one
  (`local@domain.tld`, no whitespace) - it is lower-cased and stripped
  before storing, since `send` uses it verbatim as the recipient. Refused
  (`ValueError`) for any other state: this is the manual path only, never
  a way to overwrite a verified address or re-route an already-sent row.
- **`give_up(outreach_id)`** moves `NEEDS_MANUAL_EMAIL -> UNRESOLVED`
  (terminal) for a founder whose address cannot be found, so the row stops
  appearing in `list-manual`.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.domain.states import OutreachState, assert_transition

_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PUBLISHED = "published"


class _FounderRepo(Protocol):
    def get(self, id: int): ...
    def list_by_state(self, state): ...


class _GetRepo(Protocol):
    def get(self, id: int): ...


class _OutreachRepo(Protocol):
    def get(self, id: int): ...
    def get_by_founder_id(self, founder_id: int): ...
    def upsert(self, **fields): ...


class _CandidateRepo(Protocol):
    def list_by_founder_id(self, founder_id: int): ...


@dataclass
class ManualEmailRepos:
    founder: _FounderRepo
    company: _GetRepo
    outreach: _OutreachRepo
    email_candidate: _CandidateRepo


@dataclass
class ManualItem:
    outreach: object
    founder: object
    company: object | None
    candidates: list = field(default_factory=list)   # judged rows, priority order, hint excluded
    published_hint: str | None = None                # a launch address not attributable to this founder


def normalise_address(address: str) -> str:
    """The address as it will be stored and sent to, or a `ValueError`
    naming why it cannot be: one `@`, something on both sides, a dot in
    the domain part, no whitespace."""
    cleaned = (address or "").strip().lower()
    if not _ADDRESS.match(cleaned):
        raise ValueError(f"{address!r} does not look like an email address (expected local@domain.tld)")
    return cleaned


class ResolveManualEmail:
    def __init__(self, repos: ManualEmailRepos):
        self._repos = repos

    def pending(self) -> list[ManualItem]:
        items = []
        for founder in self._repos.founder.list_by_state(state=OutreachState.NEEDS_MANUAL_EMAIL):
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            company = self._repos.company.get(founder.company_id)
            rows = list(self._repos.email_candidate.list_by_founder_id(founder.id))
            hint = next((r.address for r in rows if getattr(r, "verdict", None) == _PUBLISHED), None)
            tried = [r for r in rows if getattr(r, "verdict", None) != _PUBLISHED]
            tried.sort(key=lambda r: (getattr(r, "priority", None) is None, getattr(r, "priority", 0) or 0))
            items.append(ManualItem(outreach=outreach, founder=founder, company=company,
                                    candidates=tried, published_hint=hint))
        items.sort(key=lambda i: ((getattr(i.company, "name", None) or "").lower(),
                                  (getattr(i.founder, "full_name", None) or "").lower()))
        return items

    def set_email(self, outreach_id: int, address: str) -> str:
        """Store `address` and move to `READY_TO_DRAFT`; returns the
        normalised address actually stored."""
        outreach = self._manual_outreach(outreach_id)
        cleaned = normalise_address(address)
        assert_transition(OutreachState(outreach.state), OutreachState.READY_TO_DRAFT)
        self._repos.outreach.upsert(
            founder_id=outreach.founder_id, state=str(OutreachState.READY_TO_DRAFT),
            resolved_email=cleaned, last_error=None,
        )
        return cleaned

    def give_up(self, outreach_id: int) -> None:
        outreach = self._manual_outreach(outreach_id)
        assert_transition(OutreachState(outreach.state), OutreachState.UNRESOLVED)
        self._repos.outreach.upsert(founder_id=outreach.founder_id, state=str(OutreachState.UNRESOLVED))

    def _manual_outreach(self, outreach_id: int):
        outreach = self._repos.outreach.get(outreach_id)
        if outreach is None:
            raise ValueError(f"no outreach with id {outreach_id}")
        if OutreachState(outreach.state) is not OutreachState.NEEDS_MANUAL_EMAIL:
            raise ValueError(
                f"outreach {outreach_id} is {outreach.state}, not needs_manual_email - "
                "only a founder listed by `list-manual` can be resolved by hand"
            )
        return outreach
