"""Task 15: send every `APPROVED` outreach through the `MailSender` port,
at most `cap` per rolling 24 hours, with reserve-before-send double-send protection
(spec §13, rulings R1/R2, D5, D10, spec §18).

`SendApproved` is pure application logic: it depends only on the
`MailSender` and `Clock` ports plus a bundle of repos (`SendRepos`), all
injected by the caller (D8) — never on a concrete adapter, so this module
never imports `app.adapters`. The CLI wires `GmailSender` and the
SQLAlchemy repos together (`app.cli.send`).

The order of operations per founder is the point of this module and is
not optional:

1. **Guard.** The outreach must be `APPROVED` (`assert_transition(APPROVED
   -> SENT)` is checked up front so a stale listing can never reach the
   sender), must have a `resolved_email`, and must have its step's outbound
   draft row. A row that already carries `sent_at` while the outreach is
   still `APPROVED` is the crash-between-reserve-and-record signature: it
   is counted as `needs_manual_check`, logged, and **never re-sent** —
   the operator reconciles it against Gmail by hand (spec §13 "needs
   verification"). A row whose `approved_at` is NULL while the outreach
   says APPROVED is a data inconsistency: counted in `errors`, logged,
   never reserved (the use case asserts what the DB CHECK asserts, spec
   §13). `sequence_step != 0` raises `NotImplementedError` — Phase 1 sends
   the initial email only (ruling R-C). Then the cap: `cap` minus the
   outbound rows sent in the last 24 hours (a rolling window from the
   injected clock, ruling R-A), so re-running the CLI cannot exceed
   `daily_send_cap`.
2. **Reserve (ruling R1).** The draft-time outbound row IS the
   reservation. `message.reserve_for_send(id, now)` runs a single
   conditional `UPDATE messages SET sent_at = :now WHERE id = :id AND
   sent_at IS NULL AND approved_at IS NOT NULL` and returns its rowcount,
   and the session is **committed immediately**. Rowcount != 1 means
   someone else reserved it, or the approval is gone: skip
   (`skipped_not_reservable`), never send. Sending before this commit is
   THE double-send bug: if Gmail succeeds and the transaction then fails,
   the retry finds nothing reserved and emails the founder twice.
3. **Send.** Compose the wire text at send time (ruling R2, `compose_wire_text`
   — the stored body is untouched), call `sender.send`. A sender exception
   is re-raised after `outreach.last_error` is stamped and committed
   (type name only — never the message, which may carry provider detail):
   Gmail quota/auth errors pause the whole run rather than burning through
   the queue (spec §13), and the reservation stays put so the next run
   surfaces it as `needs_manual_check`.
4. **Record.** `gmail_message_id` / `gmail_thread_id` on the row,
   `APPROVED -> SENT`, `next_action_at = sent_at + followup_day_1` (spec
   §8 stage 7), commit.

`send_to_self` is the operator's smoke test (brief step 6, `send
--to-self`): it composes and sends the first `limit` approved drafts to
the given address **without reserving or transitioning anything**, so a
real send can follow. Nothing it does is committed.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol

from app.domain.compose import compose_wire_text, first_name
from app.domain.states import OutreachState, assert_transition

log = logging.getLogger(__name__)

_DEFAULT_FOLLOWUP_DAY_1 = 5  # spec §7 settings default (no AppSettings reader yet - deferred gap)
_CAP_WINDOW = timedelta(hours=24)  # ruling R-A: rolling window for the daily cap


@dataclass
class SendResult:
    sent: int = 0
    skipped_cap: int = 0
    skipped_not_reservable: int = 0  # reservation UPDATE hit 0 rows: never sent
    needs_manual_check: int = 0      # reserved earlier, no gmail id: never re-sent
    errors: int = 0                  # no resolved email / no draft row


class _ListByState(Protocol):
    def list_by_state(self, state): ...


class _OutreachRepo(Protocol):
    def get_by_founder_id(self, founder_id: int): ...
    def upsert(self, **fields): ...


class _MessageRepo(Protocol):
    def get_outbound(self, outreach_id: int, sequence_step: int): ...
    def reserve_for_send(self, message_id: int, now: datetime) -> int: ...
    def count_sent_since(self, since: datetime) -> int: ...
    def upsert(self, **fields): ...


class _Committable(Protocol):
    def commit(self) -> None: ...


@dataclass
class SendRepos:
    founder: _ListByState
    outreach: _OutreachRepo
    message: _MessageRepo
    session: _Committable  # the session the repos above share


class SendApproved:
    def __init__(
        self,
        sender,
        clock,
        repos: SendRepos,
        signature: str = "",
        attachment_path: str | None = None,
        followup_day_1: int = _DEFAULT_FOLLOWUP_DAY_1,
        pause_between_sends: Callable[[], None] | None = None,
    ):
        self._sender = sender
        self._clock = clock
        self._repos = repos
        self._signature = signature
        self._attachment_path = attachment_path
        self._followup_day_1 = followup_day_1
        self._pause = pause_between_sends

    def run(self, cap: int, limit: int | None = None) -> SendResult:
        result = SendResult()
        now = self._clock.now()
        # Ruling R-A (fix round 1): a ROLLING 24-hour window, not a calendar
        # day — a fixed midnight boundary would let 2x the cap out within a
        # short span straddling it.
        remaining = max(cap - self._repos.message.count_sent_since(now - _CAP_WINDOW), 0)
        if limit is not None:
            remaining = min(remaining, limit)

        for founder in self._repos.founder.list_by_state(state=OutreachState.APPROVED):
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            # Sending from anything but APPROVED is impossible: checked
            # before the cap, before the reservation, before the sender.
            assert_transition(OutreachState(outreach.state), OutreachState.SENT)
            if outreach.sequence_step != 0:
                # Ruling R-C (fix round 1): Phase 1 sends the initial email
                # only. Follow-ups (D6, spec §12) schedule relative to the
                # initial send and need `followup_day_2`; failing loudly here
                # stops Phase 2's send-worker from silently mis-scheduling.
                raise NotImplementedError("follow-ups are Phase 2")

            row = self._repos.message.get_outbound(outreach.id, outreach.sequence_step)
            if row is None:
                log.warning("founder %s is APPROVED but has no outbound draft row", founder.id)
                result.errors += 1
                continue
            if row.approved_at is None:
                # Fix round 1 (F6): the use case asserts what the DB CHECK
                # asserts (spec §13) instead of letting the reservation's
                # WHERE clause report this data inconsistency as a lost race.
                log.warning("founder %s is APPROVED but message %s has no approved_at: "
                            "not sent; approve the draft again", founder.id, row.id)
                result.errors += 1
                continue
            if row.sent_at is not None:
                # Reserved by an earlier run that never recorded a Gmail id
                # (crash between reserve and record). Never re-send.
                log.warning(
                    "message %s for founder %s was reserved at %s but has no gmail id: "
                    "needs manual check against Gmail, not re-sent",
                    row.id, founder.id, row.sent_at,
                )
                result.needs_manual_check += 1
                continue
            if not outreach.resolved_email:
                log.warning("founder %s is APPROVED but has no resolved email", founder.id)
                result.errors += 1
                continue
            if remaining <= 0:
                result.skipped_cap += 1
                continue

            if result.sent > 0 and self._pause is not None:
                self._pause()

            # -- 1. reserve, in its own committed transaction ----------------
            now = self._clock.now()
            if self._repos.message.reserve_for_send(row.id, now) != 1:
                result.skipped_not_reservable += 1
                continue
            self._repos.session.commit()

            # -- 2. send ----------------------------------------------------
            wire_text = compose_wire_text(first_name(founder.full_name), row.body, self._signature)
            try:
                gmail_message_id, gmail_thread_id = self._sender.send(
                    outreach.resolved_email, row.subject or "", wire_text, self._attachment_path
                )
            except Exception as exc:  # noqa: BLE001 — any sender failure pauses the run
                reason = type(exc).__name__
                log.error("send failed for founder %s after reservation: %s", founder.id, reason)
                self._repos.outreach.upsert(
                    founder_id=outreach.founder_id,
                    last_error=f"send failed after reservation: {reason}; needs manual check",
                )
                self._repos.session.commit()
                raise

            # -- 3. record --------------------------------------------------
            self._repos.message.upsert(
                id=row.id, gmail_message_id=gmail_message_id, gmail_thread_id=gmail_thread_id,
            )
            self._repos.outreach.upsert(
                founder_id=outreach.founder_id,
                state=str(OutreachState.SENT),
                next_action_at=now + timedelta(days=self._followup_day_1),
                last_error=None,
            )
            self._repos.session.commit()
            result.sent += 1
            remaining -= 1

        return result

    def send_to_self(self, address: str, limit: int = 1) -> int:
        """Operator smoke test: send up to `limit` approved drafts, composed
        exactly as they would go out, to `address` — no reservation, no
        state change, no commit. Returns how many were sent."""
        sent = 0
        for founder in self._repos.founder.list_by_state(state=OutreachState.APPROVED):
            if sent >= limit:
                break
            outreach = self._repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            row = self._repos.message.get_outbound(outreach.id, outreach.sequence_step)
            if row is None:
                continue
            wire_text = compose_wire_text(first_name(founder.full_name), row.body, self._signature)
            self._sender.send(address, row.subject or "", wire_text, self._attachment_path)
            sent += 1
        return sent
