"""Task 14: draft one internship enquiry per `READY_TO_DRAFT` founder
(spec §10, D17, D23, D28, D29).

`DraftOutreach` is pure application logic: it depends only on the `Drafter`
port and a bundle of repos (`DraftRepos`), both injected by the caller (D8)
— never on a concrete adapter, so this module never imports `app.adapters`.
The CLI wires `GeminiDrafter` and the SQLAlchemy repos together (see
`app.cli.draft`).

What happens per founder, in order:

1. **Build the sources the drafter may ground on, in priority order (D17):**
   `company_news` → `company_launches` → intern-appropriate `job_postings`
   → `long_description`. A posting that fails `is_intern_appropriate` (D23)
   is *excluded entirely* — it is never offered to the drafter, not even as
   a last resort, because citing a senior vacancy in an internship enquiry
   is worse than citing nothing. A `long_description` under 80 characters
   (spec §10, "when no research material exists") is likewise not offered:
   there is nothing specific in it to ground on, so it would only tempt the
   model into restating the one-liner (D17).
2. **No sources at all → manual write, no LLM call.** Straight to
   `PENDING_APPROVAL` flagged *needs manual write* (`last_error`), because
   letting the model strain a generic sentence into looking specific is
   exactly the failure this system exists to avoid.
3. **Draft, validate, retry once.** The drafter gets a context dict
   (`company`, `founder`, `available_sources`, `claims`, `sequence_step`)
   and returns `{subject, body, research_source_type, research_source_id}`.
   `validate_draft` (deterministic domain code, spec §10) runs over it with
   the resume claims. On failure, the drafter is called exactly one more
   time with `validation_errors` and `previous_draft` appended to the
   context. Never a third call: `_MAX_ATTEMPTS = 2`.
4. **Grounding resolves against every row, not just the offered ones.** The
   cited `research_source_type`/`id` is looked up in a map that also holds
   the excluded non-intern postings (flagged `intern_appropriate=False`), so
   a drafter that cites one anyway gets the precise D23 error ("cited job
   posting is not intern-appropriate") rather than a vaguer "does not
   resolve" — the reviewer sees exactly what went wrong.
5. **Persist and transition.** Valid or not, the draft is persisted as an
   outbound `messages` row (`sequence_step=0`, with its research source),
   and the outreach moves `READY_TO_DRAFT → PENDING_APPROVAL` through
   `assert_transition`. A second validation failure sets `last_error` to
   the joined errors (the *needs manual write* flag) — the rejected draft
   and its reasons are shown to the human, never silently dropped. A
   success clears any stale `last_error`.
6. **A drafter exception is per-founder.** It is counted in
   `result.errors`, the founder stays `READY_TO_DRAFT` (resumable on the
   next run), and the run continues — same reasoning as `ResolveEmails`'s
   `VerifierError` path: don't discard the drafts already made this run
   because one call blew up.

The resume profile is loaded once per run; with no profile (or no claims)
the run refuses to start (`ValueError`) rather than drafting emails that
can say nothing about the sender.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.domain.entities import Draft, ResumeClaim
from app.domain.interns import is_intern_appropriate
from app.domain.states import OutreachState, assert_transition
from app.domain.validation import SourceRow, validate_draft

_MAX_ATTEMPTS = 2  # one draft + exactly one retry (spec §10 "retry once")
_MIN_LONG_DESCRIPTION_CHARS = 80  # spec §10: below this, no research material
_MAX_LAUNCH_BODY_CHARS = 2000
_NO_MATERIAL = "needs manual write: no research material (no news, no launch, no intern-appropriate posting, description too short)"


@dataclass
class DraftResult:
    drafted: int = 0
    manual_write: int = 0
    retried: int = 0
    errors: int = 0


class _ListByState(Protocol):
    def list_by_state(self, state): ...


class _ListByCompany(Protocol):
    def list_by_company_id(self, company_id: int): ...


class _UpsertRepo(Protocol):
    def upsert(self, **fields): ...


class _ProfileRepo(Protocol):
    def get_current(self): ...


@dataclass
class DraftRepos:
    founder: _ListByState         # + get(id)
    company: _UpsertRepo          # + get(id)
    news: _ListByCompany
    launch: _ListByCompany
    job_posting: _ListByCompany
    outreach: _UpsertRepo         # + get_by_founder_id(founder_id)
    message: _UpsertRepo
    resume_profile: _ProfileRepo


@dataclass
class _Sources:
    offered: list[dict] = field(default_factory=list)          # what the drafter sees
    resolvable: dict[tuple[str, int], SourceRow] = field(default_factory=dict)  # what grounding checks


class DraftOutreach:
    def __init__(self, drafter, repos: DraftRepos):
        self._drafter = drafter
        self._repos = repos

    def run(self, limit: int | None = None) -> DraftResult:
        result = DraftResult()
        claims = self._load_claims()

        founders = self._repos.founder.list_by_state(state=OutreachState.READY_TO_DRAFT)
        if limit is not None:
            founders = founders[:limit]

        for founder in founders:
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            company = self._repos.company.get(founder.company_id)
            if company is None:
                continue

            sources = self._build_sources(company)
            if not sources.offered:
                self._finish(outreach, draft=None, errors=[_NO_MATERIAL])
                result.manual_write += 1
                continue

            context = {
                "company": {
                    "name": company.name,
                    "one_liner": getattr(company, "one_liner", None),
                    "long_description": getattr(company, "long_description", None),
                },
                "founder": {
                    "full_name": founder.full_name,
                    "title": getattr(founder, "title", None),
                    "bio": getattr(founder, "bio", None),
                },
                "available_sources": sources.offered,
                "claims": [{"kind": c.kind, "text": c.text} for c in claims],
                "sequence_step": 0,
            }

            draft: Draft | None = None
            errors: list[str] = []
            failed = False
            for attempt in range(_MAX_ATTEMPTS):
                if attempt > 0:
                    context = {**context, "validation_errors": errors,
                               "previous_draft": _draft_dict(draft)}
                    result.retried += 1
                try:
                    raw = self._drafter.draft(context)
                except Exception:  # noqa: BLE001 — any drafter failure is per-founder
                    failed = True
                    break
                draft = _to_draft(raw)
                source = sources.resolvable.get((draft.research_source_type, draft.research_source_id))
                errors = validate_draft(
                    draft, source, company.name, getattr(company, "one_liner", None), claims,
                )
                if not errors:
                    break

            if failed:
                result.errors += 1
                continue  # leave READY_TO_DRAFT; resumable next run

            self._finish(outreach, draft=draft, errors=errors)
            if errors:
                result.manual_write += 1
            else:
                result.drafted += 1

        return result

    # -- helpers ---------------------------------------------------------------

    def _load_claims(self) -> list[ResumeClaim]:
        profile = self._repos.resume_profile.get_current()
        raw_claims = list(getattr(profile, "claims", None) or []) if profile else []
        if not raw_claims:
            raise ValueError("no resume profile with claims — run `parse-resume PATH` first")
        return [
            ResumeClaim(
                kind=str(c.get("kind", "")), text=str(c.get("text", "")),
                source_span=str(c.get("source_span", "")),
                span_verified=bool(c.get("span_verified", True)),
            )
            for c in raw_claims
        ]

    def _build_sources(self, company) -> _Sources:
        s = _Sources()

        for n in self._repos.news.list_by_company_id(company.id):
            publication = getattr(n, "source", None)
            s.offered.append({"kind": "news", "id": n.id, "text": n.title, "publication": publication})
            s.resolvable[("news", n.id)] = SourceRow("news", n.title, publication, None)

        for l in self._repos.launch.list_by_company_id(company.id):
            text = _launch_text(l)
            s.offered.append({"kind": "launch", "id": l.id, "text": text})
            s.resolvable[("launch", l.id)] = SourceRow("launch", text, None, None)

        for p in self._repos.job_posting.list_by_company_id(company.id):
            ok = is_intern_appropriate(
                getattr(p, "employment_type", None), p.title, getattr(p, "min_experience", None),
            )
            text = _posting_text(p)
            # Always resolvable (so a draft citing an excluded posting gets
            # the precise D23 error), but only *offered* when appropriate.
            s.resolvable[("job_posting", p.id)] = SourceRow("job_posting", text, None, ok)
            if ok:
                s.offered.append({"kind": "job_posting", "id": p.id, "text": text})

        desc = getattr(company, "long_description", None) or ""
        if len(desc) >= _MIN_LONG_DESCRIPTION_CHARS:
            s.offered.append({"kind": "long_description", "id": company.id, "text": desc})
            s.resolvable[("long_description", company.id)] = SourceRow("long_description", desc, None, None)

        return s

    def _finish(self, outreach, draft: Draft | None, errors: list[str]) -> None:
        if draft is not None:
            self._repos.message.upsert(
                outreach_id=outreach.id,
                sequence_step=0,
                direction="outbound",
                subject=draft.subject,
                body=draft.body,
                research_source_type=draft.research_source_type,
                research_source_id=draft.research_source_id,
            )
        assert_transition(OutreachState(outreach.state), OutreachState.PENDING_APPROVAL)
        self._repos.outreach.upsert(
            founder_id=outreach.founder_id,
            state=str(OutreachState.PENDING_APPROVAL),
            last_error="; ".join(errors) if errors else None,
        )


def _to_draft(raw: dict) -> Draft:
    source_id = raw.get("research_source_id")
    try:
        source_id = int(source_id) if source_id is not None else None
    except (TypeError, ValueError):
        source_id = None
    return Draft(
        subject=str(raw.get("subject") or ""),
        body=str(raw.get("body") or ""),
        research_source_type=str(raw.get("research_source_type") or ""),
        research_source_id=source_id,
        sequence_step=0,
    )


def _draft_dict(draft: Draft | None) -> dict:
    if draft is None:
        return {}
    return {
        "subject": draft.subject, "body": draft.body,
        "research_source_type": draft.research_source_type,
        "research_source_id": draft.research_source_id,
    }


def _launch_text(launch) -> str:
    title = getattr(launch, "title", "") or ""
    body = (getattr(launch, "body", "") or "")[:_MAX_LAUNCH_BODY_CHARS]
    return f"{title}\n{body}".strip()


def _posting_text(posting) -> str:
    parts = [posting.title]
    for attr in ("employment_type", "min_experience"):
        value = getattr(posting, attr, None)
        if value:
            parts.append(str(value))
    return ", ".join(parts)  # no em dash here: the body's em-dash cap (D28) shouldn't be spent on copied source text
