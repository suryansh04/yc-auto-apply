"""Task 11 (Phase 2, spec §6): move a `SENT` founder who hasn't replied into
the next step of the follow-up sequence — or close them out once the
sequence is exhausted.

`ScheduleFollowUps` is pure application logic — it depends only on a bundle
of repos (`FollowUpRepos`) and a `clock` (D8), never on a concrete adapter,
so this module never imports `app.adapters` (enforced by
`tests/test_architecture.py`).

**What this use case does NOT do.** It writes no message and sends
nothing. A row it schedules only moves back to `READY_TO_DRAFT` with its
`sequence_step` incremented — exactly the state `DraftOutreach` already
knows how to pick up for a first email. Drafting the follow-up itself
(thread history in the prompt, the shorter-than-previous-message rule,
spec §6 points 1-3) is Task 12's job, not this one's. Nothing here ever
touches Gmail or the approval queue directly: a scheduled follow-up must
travel through the exact same draft -> review -> approve -> `SendApproved`
pipeline a first email does (D5's human gate is not negotiable, and is not
this module's to relax).

Order of operations per run:

1. **Candidates.** `OutreachRepo.list_due()` — every `SENT` row, full
   stop (review fix round 2, ruling T11-3). Every other state (`REPLIED`,
   `BOUNCED`, `CLOSED`, `DISCARDED`, `UNRESOLVED`, and anything still
   upstream of `SENT`) is excluded by the state filter alone.
   `next_action_at` is NOT filtered on here any more — see point 2 for
   why — so a row with `next_action_at = NULL` (e.g. one
   `ClassifyReply._restore_sent` could never schedule, before Task 11's
   own review fix round 1 closed that particular gap) still reaches this
   run and is evaluated the same as every other `SENT` row, entirely from
   its last outbound message's `sent_at`.
2. **Due-ness is decided ENTIRELY here, live, from `sent_at` — never from
   the stored `next_action_at`.** `next_action_at` on the row is written
   by `SendApproved` using whatever `followup_day_1`/`followup_day_2`
   were configured *at send time*; it is kept purely for display/audit,
   never read for gating. An operator can edit the `settings` row at any
   time afterward (spec §7), and this run must honour the CURRENT
   configured interval in BOTH directions — a shortened interval must be
   able to pull a row forward, not just a lengthened one push it back.
   Fix round 1 shipped a repo-level `next_action_at <= now` pre-filter
   that broke the "shorten" direction (the SQL excluded a row before the
   live recompute ever ran); fix round 2 removed it for exactly this
   reason. Each candidate is anchored to its last outbound message's
   `sent_at` and compared against `sent_at + <live interval>`. A row
   whose live interval has since grown past `now` (e.g. an operator bumps
   `followup_day_1` from 5 to 30) is left alone this run, not
   force-scheduled off a stale timestamp; a row whose interval has
   SHRUNK past `now` is now correctly picked up even though its stale
   `next_action_at` (or a `NULL` one) would have said otherwise.
3. **Branch on `sequence_step`.** Below 2 (steps 0 or 1 just sent): bump
   `sequence_step` by one and send the row back to `READY_TO_DRAFT`
   through `assert_transition` — never by writing `outreach.state`
   directly. `next_action_at` is cleared (`None`) here: the NEXT value is
   `SendApproved`'s job to set, once this step is actually drafted,
   approved and sent (exactly as it already does for step 0, spec §8
   stage 7). At 2 (the second follow-up already sent, nothing further
   defined): `SENT -> CLOSED` through `assert_transition` instead — spec
   §6 "after step 2 with no human reply, the row moves to CLOSED".
4. **Commit per row**, same reasoning as every other stage worker in this
   codebase (`PollReplies`, `ClassifyReply`): a crash mid-run keeps
   whatever was already written, and the next run's `list_due` picks up
   exactly where this one left off.

`FollowUpResult{scheduled, closed}` is a flat count of each outcome —
mirrors `SendResult`/`ClassifyResult`'s shape so callers (the CLI, the
future job runner) can log/report identically.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from app.domain.states import OutreachState, assert_transition

log = logging.getLogger(__name__)

_DEFAULT_DAY_1 = 5   # spec §7 settings default — mirrors send_approved.py
_DEFAULT_DAY_2 = 12  # spec §7 settings default


@dataclass
class FollowUpResult:
    scheduled: int = 0  # SENT -> READY_TO_DRAFT, sequence_step incremented
    closed: int = 0      # SENT -> CLOSED (past the last defined follow-up step)


class _OutreachRepo(Protocol):
    def list_due(self): ...
    def upsert(self, **fields): ...


class _MessageRepo(Protocol):
    def get_outbound(self, outreach_id: int, sequence_step: int): ...


class _SettingsRepo(Protocol):
    def get(self): ...


class _Committable(Protocol):
    def commit(self) -> None: ...


@dataclass
class FollowUpRepos:
    outreach: _OutreachRepo
    message: _MessageRepo
    session: _Committable
    settings: _SettingsRepo | None = None  # optional, same fallback shape as ClassifyRepos.settings


class ScheduleFollowUps:
    def __init__(self, repos: FollowUpRepos, clock, day_1: int = _DEFAULT_DAY_1, day_2: int = _DEFAULT_DAY_2):
        self._repos = repos
        self._clock = clock
        self._day_1 = day_1
        self._day_2 = day_2

    def run(self) -> FollowUpResult:
        result = FollowUpResult()
        now = self._clock.now()

        for outreach in self._repos.outreach.list_due():
            last_sent = self._repos.message.get_outbound(outreach.id, outreach.sequence_step)
            if last_sent is None or last_sent.sent_at is None:
                # Nothing to anchor a live due-check to (data inconsistency:
                # SENT with no recorded send). Leave it exactly as it is
                # rather than guessing; it stays visible for the next run.
                log.warning(
                    "schedule_follow_ups: outreach %s is SENT at step %s with no sent outbound "
                    "row — left unscheduled", outreach.id, outreach.sequence_step,
                )
                continue

            interval_days = self._interval_days(outreach.sequence_step)
            if last_sent.sent_at + timedelta(days=interval_days) > now:
                # The stored next_action_at was due under a stale interval;
                # the currently configured one says otherwise. Not due yet.
                continue

            if outreach.sequence_step < 2:
                self._schedule(outreach, result)
            else:
                self._close(outreach, result)

        return result

    def _interval_days(self, sequence_step: int) -> int:
        """`followup_day_1`/`followup_day_2` from the `settings` table,
        falling back to this instance's own `day_1`/`day_2` (which
        themselves default to spec §7's 5/12) when there is no settings
        repo, no row, or the row's field is `None` — same fallback shape
        `SendApproved`/`ClassifyReply` already use. Step 0 (the initial
        email, about to become follow-up 1) uses `day_1`; step 1 or 2
        (already at or past follow-up 1) uses `day_2` — spec §6 defines no
        third interval, so the wait before closing out step 2 reuses the
        step-2 interval rather than inventing an undocumented one."""
        settings_row = self._repos.settings.get() if self._repos.settings is not None else None
        if sequence_step <= 0:
            configured = getattr(settings_row, "followup_day_1", None)
            return configured if configured is not None else self._day_1
        configured = getattr(settings_row, "followup_day_2", None)
        return configured if configured is not None else self._day_2

    def _schedule(self, outreach, result: FollowUpResult) -> None:
        assert_transition(OutreachState(outreach.state), OutreachState.READY_TO_DRAFT)
        self._repos.outreach.upsert(
            founder_id=outreach.founder_id,
            state=str(OutreachState.READY_TO_DRAFT),
            sequence_step=outreach.sequence_step + 1,
            next_action_at=None,
        )
        self._repos.session.commit()
        result.scheduled += 1

    def _close(self, outreach, result: FollowUpResult) -> None:
        assert_transition(OutreachState(outreach.state), OutreachState.CLOSED)
        self._repos.outreach.upsert(
            founder_id=outreach.founder_id,
            state=str(OutreachState.CLOSED),
            next_action_at=None,
        )
        self._repos.session.commit()
        result.closed += 1
