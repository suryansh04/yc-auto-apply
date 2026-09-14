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
5. **Persist and transition.** Valid or not, the draft is persisted as the
   outbound `messages` row for `(outreach_id, sequence_step)` and the
   outreach moves `READY_TO_DRAFT → PENDING_APPROVAL` through
   `assert_transition`. A second validation failure sets `last_error` to
   the joined errors (the *needs manual write* flag) — the rejected draft
   and its reasons are shown to the human, never silently dropped. A
   success clears any stale `last_error`.

   **That outbound row IS the send reservation (fix round 1, F1 / ruling
   R1).** The schema's `uq_messages_outreach_step_outbound` index allows
   exactly one outbound row per `(outreach_id, sequence_step)`, and
   `PENDING_APPROVAL → READY_TO_DRAFT` is a legal transition (a reviewer
   sends a draft back), so a redraft must *update* the existing row —
   subject/body/source replaced, `approved_at` and `edited_by_user` reset,
   because the old approval was for the old text — never insert a second
   one. `_finish` looks the row up via `message.get_outbound(...)` first.
   `sequence_step` is read from the outreach row, not hardcoded, so the
   same code serves follow-ups once `SENT → READY_TO_DRAFT (step+1)`
   exists; for a step > 0 the previous outbound body is passed to the
   validator's follow-up-must-be-shorter rule.
6. **A drafter exception is per-founder, logged, recorded, and bounded
   (fix round 1, F3).** The exception's *type name* (never its message —
   the SDK holds the API key, and a type name is certainly safe to log) is
   written to `log` and to `outreach.last_error` without any state
   transition, so the founder stays `READY_TO_DRAFT` and is retried next
   run with the reason visible. Three *consecutive* failures abort the run
   (`result.aborted`) — a bad key or an exhausted quota fails every call,
   and burning 269 calls to learn that is the failure mode this bound
   exists to stop. A success resets the consecutive counter.

The resume profile is loaded once per run. Claims whose `source_span`
could not be found in the resume (`span_verified=False`) are excluded by
default (fix round 1, ruling R3) — a claim with a hallucinated span is the
one most likely to have a hallucinated text too — unless the operator has
read them (`show-claims`) and opts in with `keep_unverified=True`
(`draft --keep-unverified`). With no usable claims the run refuses to
start (`ValueError`) rather than drafting emails that can say nothing
about the sender.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from app.domain.entities import Draft, ResumeClaim
from app.domain.interns import is_intern_appropriate
from app.domain.states import OutreachState, assert_transition
from app.domain.targeting import OUT_OF_BAND_STATUS
from app.domain.validation import SourceRow, validate_draft

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2  # one draft + exactly one retry (spec §10 "retry once")
_MAX_CONSECUTIVE_ERRORS = 3  # fix round 1 (F3): abort a run that can't draft at all
_MIN_LONG_DESCRIPTION_CHARS = 80  # spec §10: below this, no research material
_MAX_LAUNCH_BODY_CHARS = 2000
_NO_MATERIAL = "needs manual write: no research material (no news, no launch, no intern-appropriate posting, description too short)"


@dataclass
class DraftResult:
    drafted: int = 0
    manual_write: int = 0
    retried: int = 0
    errors: int = 0
    aborted: bool = False  # fix round 1 (F3): stopped after consecutive drafter failures
    skipped_out_of_band: int = 0  # final fix wave (F13): founders at a company that left the band


class _ListByState(Protocol):
    def list_by_state(self, state): ...


class _ListByCompany(Protocol):
    def list_by_company_id(self, company_id: int): ...


class _UpsertRepo(Protocol):
    def upsert(self, **fields): ...


class _MessageRepo(Protocol):
    def upsert(self, **fields): ...
    def get_outbound(self, outreach_id: int, sequence_step: int): ...
    def list_by_outreach(self, outreach_id: int): ...


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
    message: _MessageRepo
    resume_profile: _ProfileRepo


@dataclass
class _Sources:
    offered: list[dict] = field(default_factory=list)          # what the drafter sees
    resolvable: dict[tuple[str, int], SourceRow] = field(default_factory=dict)  # what grounding checks
    publications: list[str] = field(default_factory=list)     # every news outlet name (F16, spec §10)


class DraftOutreach:
    def __init__(self, drafter, repos: DraftRepos, keep_unverified: bool = False):
        self._drafter = drafter
        self._repos = repos
        self._keep_unverified = keep_unverified

    def run(self, limit: int | None = None) -> DraftResult:
        result = DraftResult()
        claims = self._load_claims()

        founders = self._repos.founder.list_by_state(state=OutreachState.READY_TO_DRAFT)
        if limit is not None:
            founders = founders[:limit]

        consecutive_errors = 0
        for founder in founders:
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            company = self._repos.company.get(founder.company_id)
            if company is None:
                continue
            if getattr(company, "status", None) == OUT_OF_BAND_STATUS:
                # Final fix wave (F13, ruling FR-9): the company left the
                # target band since this founder was resolved; leave the
                # row READY_TO_DRAFT untouched, draft nothing for it.
                result.skipped_out_of_band += 1
                continue
            step = int(getattr(outreach, "sequence_step", 0) or 0)

            sources = self._build_sources(company)
            if not sources.offered:
                self._finish(outreach, step, draft=None, errors=[_NO_MATERIAL])
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
                "sequence_step": step,
                "thread_history": self._thread_history(outreach, step),
            }
            # Fix round 1 (F4): facts about *them* that the prompt hands the
            # model, so the claims rule must not treat them as fabrications.
            extra_allowed = [getattr(company, "long_description", None) or "", founder.full_name]
            previous_body = self._previous_body(outreach, step)

            draft: Draft | None = None
            errors: list[str] = []
            failure: BaseException | None = None
            for attempt in range(_MAX_ATTEMPTS):
                if attempt > 0:
                    context = {**context, "validation_errors": errors,
                               "previous_draft": _draft_dict(draft)}
                    result.retried += 1
                try:
                    raw = self._drafter.draft(context)
                except Exception as exc:  # noqa: BLE001 — any drafter failure is per-founder
                    failure = exc
                    break
                draft = _to_draft(raw, step)
                source = sources.resolvable.get((draft.research_source_type, draft.research_source_id))
                errors = validate_draft(
                    draft, source, company.name, getattr(company, "one_liner", None), claims,
                    previous_body=previous_body, extra_allowed_text=extra_allowed,
                    all_publications=sources.publications,
                )
                if not errors:
                    break

            if failure is not None:
                # Fix round 1 (F3): type name only — never str(exc), which
                # could carry provider detail we don't control.
                reason = type(failure).__name__
                log.warning("drafter failed for founder %s: %s", founder.id, reason)
                self._repos.outreach.upsert(
                    founder_id=outreach.founder_id, last_error=f"drafter failed: {reason}",
                )
                result.errors += 1
                consecutive_errors += 1
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    log.warning("aborting draft run after %d consecutive drafter failures",
                                consecutive_errors)
                    result.aborted = True
                    break
                continue  # leave READY_TO_DRAFT; resumable next run

            consecutive_errors = 0
            self._finish(outreach, step, draft=draft, errors=errors)
            if errors:
                result.manual_write += 1
            else:
                result.drafted += 1

        return result

    # -- helpers ---------------------------------------------------------------

    def _load_claims(self) -> list[ResumeClaim]:
        return load_resume_claims(self._repos.resume_profile, keep_unverified=self._keep_unverified)

    def _previous_body(self, outreach, step: int) -> str | None:
        if step <= 0:
            return None
        previous = self._repos.message.get_outbound(outreach.id, step - 1)
        return getattr(previous, "body", None) if previous is not None else None

    def _thread_history(self, outreach, step: int) -> list[dict]:
        """Task 12: what a follow-up's prompt is shown of the thread so
        far — every prior OUTBOUND step (never the one being drafted right
        now, so a redraft of the same step doesn't quote itself back at the
        model) plus every INBOUND reply regardless of step, because an
        auto-reply may have landed on the thread and the model should know
        the founder (or their inbox) already said something back. A first
        email (`step == 0`) has nothing before it — empty, no LLM call
        wasted building it."""
        if step <= 0:
            return []
        messages = self._repos.message.list_by_outreach(outreach.id)
        relevant = [
            m for m in messages
            if m.direction == "inbound" or (m.direction == "outbound" and m.sequence_step < step)
        ]
        ordered = sorted(relevant, key=_message_sort_key)
        return [
            {
                "direction": m.direction,
                "sequence_step": m.sequence_step,
                "subject": getattr(m, "subject", None),
                "body": m.body,
                # Fix round 1 (ruling T12-2): an inbound row here can only
                # ever be machine-labelled (AUTO_REPLY/OUT_OF_OFFICE) or not
                # yet classified — a human label moves the outreach to
                # REPLIED before it ever reaches READY_TO_DRAFT again. The
                # prompt needs this to avoid telling the model a bot's
                # out-of-office is the founder speaking.
                "label": getattr(m, "label", None),
            }
            for m in ordered
        ]

    def _build_sources(self, company) -> _Sources:
        return build_sources(self._repos.news, self._repos.launch, self._repos.job_posting, company)

    def _finish(self, outreach, step: int, draft: Draft | None, errors: list[str]) -> None:
        # The transition is checked BEFORE anything is written, so an
        # illegal state never gets a half-persisted draft.
        assert_transition(OutreachState(outreach.state), OutreachState.PENDING_APPROVAL)
        if draft is not None:
            fields = dict(
                outreach_id=outreach.id,
                sequence_step=step,
                direction="outbound",
                subject=draft.subject,
                body=draft.body,
                research_source_type=draft.research_source_type,
                research_source_id=draft.research_source_id,
                approved_at=None,        # a redraft voids any earlier approval
                edited_by_user=False,
            )
            existing = self._repos.message.get_outbound(outreach.id, step)
            if existing is not None:
                fields["id"] = existing.id
            self._repos.message.upsert(**fields)
        self._repos.outreach.upsert(
            founder_id=outreach.founder_id,
            state=str(OutreachState.PENDING_APPROVAL),
            last_error="; ".join(errors) if errors else None,
        )


def load_resume_claims(resume_profile_repo, keep_unverified: bool = False) -> list[ResumeClaim]:
    """The resume claims a draft may cite, or a `ValueError` naming the fix.
    Module-level (Task 16) so `ReviewOutreach.edit` holds a human-written
    body to exactly the claims the drafter was held to."""
    profile = resume_profile_repo.get_current()
    raw_claims = list(getattr(profile, "claims", None) or []) if profile else []
    if not raw_claims:
        raise ValueError("no resume profile with claims — run `parse-resume PATH` first")
    claims = [
        ResumeClaim(
            kind=str(c.get("kind", "")), text=str(c.get("text", "")),
            source_span=str(c.get("source_span", "")),
            span_verified=bool(c.get("span_verified", True)),
        )
        for c in raw_claims
    ]
    if not keep_unverified:
        claims = [c for c in claims if c.span_verified]
    if not claims:
        raise ValueError(
            "every resume claim has an unverified source_span; read them with "
            "`show-claims` and re-run with --keep-unverified to use them anyway"
        )
    return claims


def build_sources(news_repo, launch_repo, job_posting_repo, company) -> _Sources:
    """Every research row for `company` as the drafter sees it (`offered`,
    priority order, D17/D23) and as grounding resolves it (`resolvable`,
    including excluded postings). Module-level (Task 16) so the review
    screen resolves a draft's citation exactly as the validator did."""
    s = _Sources()

    for n in news_repo.list_by_company_id(company.id):
        publication = getattr(n, "source", None)
        s.offered.append({"kind": "news", "id": n.id, "text": n.title, "publication": publication})
        s.resolvable[("news", n.id)] = SourceRow("news", n.title, publication, None)
        if publication and publication not in s.publications:
            s.publications.append(publication)

    for l in launch_repo.list_by_company_id(company.id):
        text = _launch_text(l)
        s.offered.append({"kind": "launch", "id": l.id, "text": text})
        s.resolvable[("launch", l.id)] = SourceRow("launch", text, None, None)

    for p in job_posting_repo.list_by_company_id(company.id):
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


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _message_sort_key(message) -> datetime:
    """Chronological order for `thread_history`: outbound rows are
    timestamped by `sent_at`, inbound rows by `received_at` (mirrors
    `app.api.routers.thread._sort_key`). Falls back to the epoch, and
    normalises a naive datetime to UTC, so a row missing its timestamp (or
    a fake row built without tzinfo) sorts first rather than raising a
    naive/aware comparison error."""
    ts = getattr(message, "sent_at", None) or getattr(message, "received_at", None)
    if ts is None:
        return _EPOCH
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _to_draft(raw: dict, step: int) -> Draft:
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
        sequence_step=step,
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
