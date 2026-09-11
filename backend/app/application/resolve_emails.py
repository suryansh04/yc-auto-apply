"""Task 12: resolve each founder's email address (spec §9, D18, D30).

`ResolveEmails` is pure application logic: it depends only on the
`EmailVerifier` port and a small bundle of repos (`ResolveRepos`), both
injected by the caller (D8) — never on a concrete adapter, so this module
never imports `app.adapters`. The CLI wires the real `MyEmailVerifierClient`
and SQLAlchemy repos together (see `app.cli.resolve`).

Ordering (spec §9) is load-bearing and each step exists for a reason:

1. **Published email first (D18).** If a founder's company has a launch
   post with a `published_email`, use it directly — verdict `published`,
   zero API calls. A launch is written by one person, so attributing its
   address to a co-founder could email the wrong person: this short-circuit
   only fires when the company has exactly one founder, or the founder's
   first or last name appears in the launch body (a proxy for "this founder
   wrote or is named in this launch"). When a published email exists but
   can't be attributed to the founder being resolved, it is still persisted
   as an `email_candidates` row (verdict `published`) so a human reviewing
   a `NEEDS_MANUAL_EMAIL` founder sees it as a hint — it is never
   auto-assigned to `resolved_email` in that case, and resolution falls
   through to the normal candidate search below.
2. **Verify candidates in priority order, stop at the first `valid`.**
   `generate_candidates` orders patterns by how common they are at
   startups; stopping early is what keeps a full run of ~269 founders
   inside a 100/day free-tier quota (spec §9: ~2.5 verifications/founder).
3. **`catch_all` never guesses.** A catch-all domain accepts every address,
   so a "valid" verdict there is meaningless. The first `catch_all`
   encountered stops that founder's search immediately (further candidates
   against the same domain would likely also come back catch-all, wasting
   quota) and routes to `NEEDS_MANUAL_EMAIL` with no `resolved_email` set —
   the ranked candidates already persisted, plus the founder's LinkedIn
   URL, are what a human reviewer works from.
4. **All `invalid`/`unknown`, no `valid` or `catch_all`.** Once every
   candidate has been tried, the founder also routes to
   `NEEDS_MANUAL_EMAIL`.
5. **Quota exhaustion pauses, never fails.** The verifier signals this via
   `QuotaExhausted` (the real adapter) or a bare `RuntimeError` (the offline
   `tests.fakes.FakeVerifier`, which does not import `app.ports`). Either
   one stops the whole run immediately: `result.paused_on_quota` is set,
   and every founder not yet fully processed — including whichever one was
   mid-search when the exception hit — is left in whatever state it was
   already in. Nothing moves to `FAILED`; the free tier renews tomorrow
   (D30), so a paused run is expected to simply resume on the next
   invocation, picking up where quota ran out via the persisted
   `email_candidates` rows (step 2 below).

Every verdict this use case obtains — including `published` — is persisted
as an `email_candidates` row keyed on `(founder_id, address)` before moving
on, so a re-run never re-verifies an address a prior run already judged;
`_already_verified` reads those rows back in before spending any of this
run's verification budget on a founder.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.candidates import generate_candidates
from app.domain.states import OutreachState, assert_transition
from app.ports import QuotaExhausted

_PROVIDER = "myemailverifier"


@dataclass
class ResolveResult:
    resolved: int = 0
    manual: int = 0
    verifications_used: int = 0
    paused_on_quota: bool = False


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
    def __init__(self, verifier, repos: ResolveRepos):
        self._verifier = verifier
        self._repos = repos

    def run(self, limit: int | None = None) -> ResolveResult:
        result = ResolveResult()
        founders = self._repos.founder.list_by_state(state=OutreachState.NEEDS_EMAIL)
        if limit is not None:
            founders = founders[:limit]

        for founder in founders:
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue

            published = self._attributable_published_email(founder)
            if published is not None:
                self._persist_verdict(founder.id, published, None, 0, "published")
                self._transition(outreach, OutreachState.READY_TO_DRAFT, resolved_email=published)
                result.resolved += 1
                continue

            already = self._already_verified(founder.id)
            candidates = generate_candidates(founder.full_name, self._domain_for(founder))

            resolved_address = None
            quota_hit = False
            for candidate in candidates:
                if candidate.address in already:
                    verdict = already[candidate.address]
                else:
                    try:
                        verdict = self._verifier.verify(candidate.address)
                    except (QuotaExhausted, RuntimeError):
                        quota_hit = True
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

        name_tokens = [t for t in founder.full_name.split() if t]
        body = (getattr(launch, "body", None) or "").lower()
        if any(token.lower() in body for token in name_tokens):
            return published

        # Ambiguous: multiple founders, no name match near the address.
        # Surface as a hint on the manual-review row rather than guessing.
        self._persist_verdict(founder.id, published, None, 0, "published")
        return None

    def _transition(self, outreach, new_state: OutreachState, **fields) -> None:
        assert_transition(OutreachState(outreach.state), new_state)
        self._repos.outreach.upsert(founder_id=outreach.founder_id, state=str(new_state), **fields)
