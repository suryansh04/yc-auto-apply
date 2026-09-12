"""Task 12: resolve each founder's email address (spec §9, D18, D30).

`ResolveEmails` is pure application logic: it depends only on the
`EmailVerifier`/`Clock` ports and a small bundle of repos (`ResolveRepos`),
both injected by the caller (D8) — never on a concrete adapter, so this
module never imports `app.adapters`. The CLI wires the real
`MyEmailVerifierClient` and SQLAlchemy repos together (see `app.cli.resolve`).

Ordering (spec §9) is load-bearing and each step exists for a reason:

0. **`NEW` rows are picked up too (Task 16).** `ScrapeCompanyPage` creates
   every new founder in `NEW`; this stage is the first attempt on them, so
   it steps each `NEW -> NEEDS_EMAIL` (state machine) before resolving.
   Rows already in `NEEDS_EMAIL` are processed first, then `NEW` ones.
1. **Published email first (D18).** If a founder's company has a launch
   post with a `published_email`, use it directly — verdict `published`,
   zero API calls. A launch is written by one person, so attributing its
   address to a co-founder could email the wrong person: this short-circuit
   only fires when the company has exactly one founder, or the founder's
   first name or full name appears, `\\b`-bounded, within ~200 characters
   of the `published_email` occurrence in the launch body (never a bare
   last name — see fix round 1 below). When a published email exists but
   can't be attributed to the founder being resolved, it is still
   persisted as an `email_candidates` row (verdict `published`) so a human
   reviewing a `NEEDS_MANUAL_EMAIL` founder sees it as a hint — it is
   never auto-assigned to `resolved_email` in that case, and resolution
   falls through to the normal candidate search below.
2. **No domain, no verification.** A company with no known `domain` has no
   addresses to generate; skip the verifier entirely (there is nothing
   real to check) and route straight to `NEEDS_MANUAL_EMAIL`.
3. **Verify candidates in priority order, stop at the first `valid`.**
   `generate_candidates` orders patterns by how common they are at
   startups; stopping early is what keeps a full run of ~269 founders
   inside a 100/day free-tier quota (spec §9: ~2.5 verifications/founder).
4. **`catch_all` never guesses.** A catch-all domain accepts every address,
   so a "valid" verdict there is meaningless. The first `catch_all`
   encountered stops that founder's search immediately (further candidates
   against the same domain would likely also come back catch-all, wasting
   quota) and routes to `NEEDS_MANUAL_EMAIL` with no `resolved_email` set —
   the ranked candidates already persisted, plus the founder's LinkedIn
   URL, are what a human reviewer works from.
5. **All `invalid`/`unknown`, no `valid` or `catch_all`.** Once every
   candidate has been tried, the founder also routes to
   `NEEDS_MANUAL_EMAIL`.
6. **Quota exhaustion pauses the whole run, never fails.** The verifier
   signals this via `QuotaExhausted` (`app.ports`). It stops the whole run
   immediately: `result.paused_on_quota` is set, and every founder not yet
   fully processed — including whichever one was mid-search when the
   exception hit — is left in whatever state it was already in. Nothing
   moves to `FAILED`; the free tier renews tomorrow (D30), so a paused run
   is expected to simply resume on the next invocation, picking up where
   quota ran out via the persisted `email_candidates` rows (see below).
7. **A per-address verification failure pauses only that founder.** The
   verifier signals this via `VerifierError` (`app.ports`) — a bad API
   key, a 5xx, a transport failure. Unlike quota exhaustion this is not
   expected to affect the rest of the run, so only the current founder is
   left `NEEDS_EMAIL` (counted in `result.errors`) and the run continues
   with the next founder, rather than discarding every verdict already
   paid for earlier in the run by aborting outright.

Every verdict this use case obtains — including `published` — is persisted
as an `email_candidates` row (stamped with `verified_at` via the injected
`Clock` port) keyed on `(founder_id, address)` before moving on, so a
re-run never re-verifies an address a prior run already judged;
`_already_verified` reads those rows back in before spending any of this
run's verification budget on a founder.

Fix round 1 (review of commit `c6c0e8e`), summary of what changed here:
- dropped the bare `except RuntimeError` around quota handling (masked
  real bugs as a quota pause) — `tests.fakes.FakeVerifier` now raises
  `app.ports.QuotaExhausted` directly, so only that type is ever caught
  for the pause path;
- added the `VerifierError` per-founder path (point 7 above), new;
- published-email attribution went from an unbounded substring match over
  first-or-last name anywhere in the body (misattributed a co-founder's
  address whenever the *other* founder's initials or surname happened to
  appear as a substring of an unrelated word — e.g. "Ng" inside
  "building") to a `\\b`-bounded regex over first name or full name only,
  required within a fixed window around the address itself;
- a company with no `domain` used to still generate and verify seven
  garbage `...@` candidates; it now skips the verifier entirely.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.domain.candidates import generate_candidates
from app.domain.states import OutreachState, assert_transition
from app.ports import QuotaExhausted, VerifierError

_PROVIDER = "myemailverifier"

# Fix round 1: how close the founder's name must appear to the published
# address for the launch to be attributed to them — "near the address"
# per spec §9 Step 0(b), not "anywhere in the body".
_NAME_PROXIMITY_CHARS = 200


class _UTCClock:
    """Default `Clock` (real time) used when `ResolveEmails` isn't given
    one explicitly — every existing call site (`ResolveEmails(v, repos)`,
    matching the task-12 brief's own test bodies) passes only two
    positional arguments, so `clock` must default to something real rather
    than being required."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class ResolveResult:
    resolved: int = 0
    manual: int = 0
    verifications_used: int = 0
    paused_on_quota: bool = False
    errors: int = 0  # founders left NEEDS_EMAIL after a VerifierError (fix round 1)


class _ListByState(Protocol):
    def list_by_state(self, state): ...


class _ListByCompany(Protocol):
    def list_by_company_id(self, company_id: int): ...


class _UpsertRepo(Protocol):
    def upsert(self, **fields): ...


@dataclass
class ResolveRepos:
    founder: _ListByState        # + get(id), list_by_company_id(company_id)
    company: _UpsertRepo         # + get(id)
    launch: _ListByCompany
    outreach: _UpsertRepo        # + get_by_founder_id(founder_id)
    email_candidate: _UpsertRepo  # + list_by_founder_id(founder_id)


class ResolveEmails:
    def __init__(self, verifier, repos: ResolveRepos, clock=None):
        self._verifier = verifier
        self._repos = repos
        self._clock = clock or _UTCClock()

    def run(self, limit: int | None = None) -> ResolveResult:
        result = ResolveResult()
        # Task 16 (the offline end-to-end run surfaced it): `ScrapeCompanyPage`
        # creates each new founder's outreach row in `NEW` (spec §8 stage 2)
        # and nothing else performed `NEW -> NEEDS_EMAIL`, so this stage
        # never saw a freshly scraped founder. `NEW` means "nothing
        # attempted"; this is the first attempt, so it steps the row into
        # `NEEDS_EMAIL` (through the state machine) before resolving.
        # Rows already in `NEEDS_EMAIL` (paused/errored earlier) go first.
        founders = (self._repos.founder.list_by_state(state=OutreachState.NEEDS_EMAIL)
                    + self._repos.founder.list_by_state(state=OutreachState.NEW))
        if limit is not None:
            founders = founders[:limit]

        for founder in founders:
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            if OutreachState(outreach.state) is OutreachState.NEW:
                self._transition(outreach, OutreachState.NEEDS_EMAIL)

            published = self._attributable_published_email(founder)
            if published is not None:
                self._persist_verdict(founder.id, published, None, 0, "published")
                self._transition(outreach, OutreachState.READY_TO_DRAFT, resolved_email=published)
                result.resolved += 1
                continue

            domain = self._domain_for(founder)
            if not domain:
                # Fix round 1 (major): nothing to verify — generating
                # candidates against an empty domain used to burn up to 7
                # credits on `...@` garbage before landing here anyway.
                self._transition(outreach, OutreachState.NEEDS_MANUAL_EMAIL)
                result.manual += 1
                continue

            already = self._already_verified(founder.id)
            candidates = generate_candidates(founder.full_name, domain)

            resolved_address = None
            quota_hit = False
            verifier_error = False
            for candidate in candidates:
                if candidate.address in already:
                    verdict = already[candidate.address]
                else:
                    try:
                        verdict = self._verifier.verify(candidate.address)
                    except QuotaExhausted:
                        quota_hit = True
                        break
                    except VerifierError:
                        # Fix round 1 (major): a single bad response used
                        # to propagate all the way out of `run`, and the
                        # CLI's session was never committed — discarding
                        # every verdict already paid for earlier in this
                        # same run. Now it only takes this one founder out
                        # of this run; everything already persisted stays
                        # persisted, and the run continues.
                        verifier_error = True
                        break
                    result.verifications_used += 1
                    self._persist_verdict(
                        founder.id, candidate.address, candidate.pattern,
                        candidate.priority, verdict,
                    )
                    already[candidate.address] = verdict

                if verdict == "valid":
                    resolved_address = candidate.address
                    break
                if verdict == "catch_all":
                    break  # never guess on a catch-all domain
                # invalid / unknown: keep trying the next candidate

            if quota_hit:
                result.paused_on_quota = True
                break  # stop the whole run; every remaining row stays as-is

            if verifier_error:
                result.errors += 1
                continue  # leave this founder NEEDS_EMAIL; move on

            if resolved_address is not None:
                self._transition(outreach, OutreachState.READY_TO_DRAFT, resolved_email=resolved_address)
                result.resolved += 1
            else:
                self._transition(outreach, OutreachState.NEEDS_MANUAL_EMAIL)
                result.manual += 1

        return result

    def _domain_for(self, founder) -> str:
        company = self._repos.company.get(founder.company_id)
        return (getattr(company, "domain", None) or "") if company else ""

    def _already_verified(self, founder_id: int) -> dict:
        return {c.address: c.verdict for c in self._repos.email_candidate.list_by_founder_id(founder_id)}

    def _persist_verdict(self, founder_id, address, pattern, priority, verdict) -> None:
        self._repos.email_candidate.upsert(
            founder_id=founder_id, address=address, pattern=pattern,
            priority=priority, verdict=verdict, provider=_PROVIDER,
            verified_at=self._clock.now(),
        )

    def _attributable_published_email(self, founder) -> str | None:
        launches = self._repos.launch.list_by_company_id(founder.company_id)
        launch = next((l for l in launches if getattr(l, "published_email", None)), None)
        if launch is None:
            return None
        published = launch.published_email

        founders_at_company = self._repos.founder.list_by_company_id(founder.company_id)
        if len(founders_at_company) == 1:
            return published

        body = getattr(launch, "body", None) or ""
        if _name_near_address(founder.full_name, body, published):
            return published

        # Ambiguous: multiple founders, no name match near the address.
        # Surface as a hint on the manual-review row rather than guessing.
        self._persist_verdict(founder.id, published, None, 0, "published")
        return None

    def _transition(self, outreach, new_state: OutreachState, **fields) -> None:
        assert_transition(OutreachState(outreach.state), new_state)
        self._repos.outreach.upsert(founder_id=outreach.founder_id, state=str(new_state), **fields)


def _name_near_address(full_name: str, body: str, address: str) -> bool:
    """Fix round 1 (major): matches the founder's first name or full name
    only — never a bare last name, which is short enough to false-positive
    as a substring of an unrelated word (spec §9 Step 0(b) requires
    attributing a launch's address to the *right* founder, exactly the
    guarantee a loose match undermines) — `\\b`-bounded, and only within
    `_NAME_PROXIMITY_CHARS` characters of where `address` itself occurs in
    `body`. An address that doesn't occur in the body at all (a malformed
    or already-stripped launch) never attributes."""
    idx = body.lower().find(address.lower())
    if idx == -1:
        return False

    window_start = max(0, idx - _NAME_PROXIMITY_CHARS)
    window_end = idx + len(address) + _NAME_PROXIMITY_CHARS
    window = body[window_start:window_end]

    tokens = [t for t in full_name.split() if t]
    if not tokens:
        return False
    first_name = tokens[0]

    for candidate_name in (full_name, first_name):
        pattern = r"\b" + re.escape(candidate_name) + r"\b"
        if re.search(pattern, window, flags=re.IGNORECASE):
            return True
    return False
