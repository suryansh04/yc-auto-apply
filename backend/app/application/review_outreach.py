"""Task 16: the human approval queue (spec D5, §6, §13 "approval cannot be
bypassed", ruling R2) - approve / edit / reject / skip over
`PENDING_APPROVAL` rows.

`ReviewOutreach` is pure application logic over a bundle of repos
(`ReviewRepos`); it never imports `app.adapters`, never commits, and never
reads a key or opens an editor. `app.cli.review` owns the terminal loop
(rendering, keys, `$EDITOR`) and commits once after each action.

What each action guarantees:

- **`pending()`** lists every `PENDING_APPROVAL` founder with the context
  a reviewer needs to judge the draft without re-researching the company:
  outreach, founder, company, the step's outbound draft row (`None` for a
  row `DraftOutreach` flagged *needs manual write* without ever calling
  the drafter) and the cited source resolved exactly as grounding resolved
  it (`build_sources`). Ordered by the spec §8 `rank_key`: companies with
  a news item first, then a launch, then the rest; ties by `team_size`
  ascending, then founder name - never by postings or `isHiring` (D24).
- **`approve(outreach_id, now)`** stamps `approved_at = now` on the step's
  outbound row and moves the outreach `PENDING_APPROVAL -> APPROVED`
  through `assert_transition`. That timestamp is what the DB CHECK
  `sent_requires_approved` and `reserve_for_send`'s `approved_at IS NOT
  NULL` predicate key off, so approving here is the one act that makes a
  send possible. Refused (`ValueError`) when there is no draft row, or when
  the row is flagged (`outreach.last_error`) and the body has not been
  edited: a draft the validator rejected twice must be rewritten by the
  human, not waved through as-is.
- **`edit(outreach_id, subject, body)`** re-runs the same deterministic
  validator the drafter faced (`validate_draft` with the resume claims,
  the cited source, the company facts and, for a follow-up, the previous
  body) and returns its errors *without persisting anything* on failure.
  On success it writes subject/body to the step's outbound row (creating
  the row for a never-drafted manual write, with no citation), sets
  `edited_by_user=True`, clears `approved_at` (an edit is never an
  approval) and clears `last_error` (the flag is discharged). The stored
  citation is kept: the reviewer can rewrite the words, not what they are
  grounded on. When there is no resolvable citation (manual write, no
  material) the grounding rules cannot apply, so only the style and
  resume-claims rules run - disclosed in the task report.
- **`reject(outreach_id)`** moves `PENDING_APPROVAL -> DISCARDED` (the
  state machine's only reject target; terminal).
- **`skip(outreach_id)`** does nothing; it exists so the CLI's dispatch is
  uniform.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.application.draft_outreach import build_sources, load_resume_claims
from app.domain.entities import Draft
from app.domain.states import OutreachState, assert_transition
from app.domain.validation import SourceRow, validate_claims, validate_draft, validate_style


class _ListByState(Protocol):
    def list_by_state(self, state): ...


class _ListByCompany(Protocol):
    def list_by_company_id(self, company_id: int): ...


class _GetRepo(Protocol):
    def get(self, id: int): ...


class _OutreachRepo(Protocol):
    def get(self, id: int): ...
    def get_by_founder_id(self, founder_id: int): ...
    def upsert(self, **fields): ...


class _MessageRepo(Protocol):
    def get_outbound(self, outreach_id: int, sequence_step: int): ...
    def upsert(self, **fields): ...


class _ProfileRepo(Protocol):
    def get_current(self): ...


@dataclass
class ReviewRepos:
    founder: _ListByState         # + get(id)
    company: _GetRepo
    outreach: _OutreachRepo
    message: _MessageRepo
    news: _ListByCompany
    launch: _ListByCompany
    job_posting: _ListByCompany
    resume_profile: _ProfileRepo


@dataclass
class ReviewItem:
    outreach: object
    founder: object
    company: object
    message: object | None        # the step's outbound draft row; None = never drafted
    source: SourceRow | None      # the cited research row, resolved; None = no citation


class ReviewOutreach:
    def __init__(self, repos: ReviewRepos, keep_unverified: bool = False):
        self._repos = repos
        self._keep_unverified = keep_unverified

    # -- listing ---------------------------------------------------------------

    def pending(self) -> list[ReviewItem]:
        items = []
        for founder in self._repos.founder.list_by_state(state=OutreachState.PENDING_APPROVAL):
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            items.append(self._item(outreach, founder))
        items.sort(key=self._rank_key)
        return items

    def load(self, outreach_id: int) -> ReviewItem:
        outreach = self._outreach(outreach_id)
        founder = self._repos.founder.get(outreach.founder_id)
        if founder is None:
            raise ValueError(f"outreach {outreach_id} has no founder")
        return self._item(outreach, founder)

    # -- actions ---------------------------------------------------------------

    def approve(self, outreach_id: int, now: datetime) -> None:
        outreach = self._outreach(outreach_id)
        assert_transition(OutreachState(outreach.state), OutreachState.APPROVED)
        row = self._repos.message.get_outbound(outreach.id, outreach.sequence_step)
        if row is None:
            raise ValueError("refusing to approve: there is no draft yet - press [e] to edit (write) one first")
        if outreach.last_error and not row.edited_by_user:
            raise ValueError(
                "refusing to approve: this draft is flagged NEEDS MANUAL WRITE "
                f"({outreach.last_error}) - press [e] to edit it before approving"
            )
        self._repos.message.upsert(id=row.id, approved_at=now)
        self._repos.outreach.upsert(founder_id=outreach.founder_id, state=str(OutreachState.APPROVED))

    def reject(self, outreach_id: int) -> None:
        outreach = self._outreach(outreach_id)
        assert_transition(OutreachState(outreach.state), OutreachState.DISCARDED)
        self._repos.outreach.upsert(founder_id=outreach.founder_id, state=str(OutreachState.DISCARDED))

    def edit(self, outreach_id: int, subject: str, body: str) -> list[str]:
        outreach = self._outreach(outreach_id)
        if OutreachState(outreach.state) is not OutreachState.PENDING_APPROVAL:
            raise ValueError(f"only a PENDING_APPROVAL draft can be edited (this one is {outreach.state})")
        founder = self._repos.founder.get(outreach.founder_id)
        company = self._repos.company.get(founder.company_id) if founder is not None else None
        if founder is None or company is None:
            raise ValueError(f"outreach {outreach_id} has no founder/company")
        step = int(getattr(outreach, "sequence_step", 0) or 0)
        row = self._repos.message.get_outbound(outreach.id, step)
        source_type = getattr(row, "research_source_type", None) if row is not None else None
        source_id = getattr(row, "research_source_id", None) if row is not None else None
        source = self._resolve_source(company, source_type, source_id)

        subject, body = subject.strip(), body.strip()
        errors = []
        if not subject:
            errors.append("subject is empty")
        if not body:
            errors.append("body is empty")
        if errors:
            return errors
        draft = Draft(subject=subject, body=body, research_source_type=source_type or "",
                      research_source_id=source_id, sequence_step=step)
        errors = self._validate(draft, source, company, founder, self._previous_body(outreach, step))
        if errors:
            return errors

        fields = dict(
            outreach_id=outreach.id, sequence_step=step, direction="outbound",
            subject=subject, body=body, edited_by_user=True, approved_at=None,
        )
        if row is not None:
            fields["id"] = row.id
        else:
            fields.update(research_source_type=None, research_source_id=None)
        self._repos.message.upsert(**fields)
        self._repos.outreach.upsert(founder_id=outreach.founder_id, last_error=None)
        return []

    def skip(self, outreach_id: int) -> None:
        """Leave the row exactly as it is."""

    # -- helpers ---------------------------------------------------------------

    def _outreach(self, outreach_id: int):
        outreach = self._repos.outreach.get(outreach_id)
        if outreach is None:
            raise ValueError(f"no outreach with id {outreach_id}")
        return outreach

    def _item(self, outreach, founder) -> ReviewItem:
        company = self._repos.company.get(founder.company_id)
        message = self._repos.message.get_outbound(outreach.id, int(getattr(outreach, "sequence_step", 0) or 0))
        source = None
        if message is not None and company is not None:
            source = self._resolve_source(company, message.research_source_type, message.research_source_id)
        return ReviewItem(outreach=outreach, founder=founder, company=company, message=message, source=source)

    def _resolve_source(self, company, kind: str | None, source_id: int | None) -> SourceRow | None:
        if not kind or source_id is None:
            return None
        sources = build_sources(self._repos.news, self._repos.launch, self._repos.job_posting, company)
        return sources.resolvable.get((kind, source_id))

    def _previous_body(self, outreach, step: int) -> str | None:
        if step <= 0:
            return None
        previous = self._repos.message.get_outbound(outreach.id, step - 1)
        return getattr(previous, "body", None) if previous is not None else None

    def _validate(self, draft: Draft, source: SourceRow | None, company, founder,
                  previous_body: str | None) -> list[str]:
        claims = load_resume_claims(self._repos.resume_profile, keep_unverified=self._keep_unverified)
        name = getattr(company, "name", None)
        one_liner = getattr(company, "one_liner", None)
        # The same "facts about them" the drafter's validation allowed (F4).
        extra_allowed = [getattr(company, "long_description", None) or "", founder.full_name]
        if source is not None:
            return validate_draft(draft, source, name, one_liner, claims,
                                  previous_body=previous_body, extra_allowed_text=extra_allowed)
        # No resolvable citation (a manual write with no research material,
        # or a drafter citation that never resolved): grounding cannot be
        # checked, so the style and resume-claims rules stand alone.
        allowed = [name or "", one_liner or "", *extra_allowed]
        return validate_style(draft, previous_body) + validate_claims(draft, claims, allowed_text=allowed)

    def _rank_key(self, item: ReviewItem):
        company = item.company
        if company is None:
            return (3, float("inf"), item.founder.full_name or "")
        if self._repos.news.list_by_company_id(company.id):
            strength = 0
        elif self._repos.launch.list_by_company_id(company.id):
            strength = 1
        else:
            strength = 2
        team_size = getattr(company, "team_size", None)
        return (strength, team_size if team_size is not None else float("inf"),
                item.founder.full_name or "")
