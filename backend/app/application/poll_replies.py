"""Task 8 (Phase 2, spec §5): turn new Gmail mail into inbound `messages`
rows. `PollReplies` is pure application logic — it depends only on an
injected `reader` (the `GmailReader`-shaped port from Task 7) plus a
bundle of repos (`PollRepos`) and a `clock`, never on a concrete adapter
(D8) — so this module never imports `app.adapters` (enforced by
`tests/test_architecture.py`).

Classification is explicitly NOT this module's job (Task 9's): a reply
lands here as an inbound row with `label=None` and the outreach's state
is left untouched — the one exception being a bounce (see below), which
is unambiguous enough to detect here rather than waiting on an LLM call.

Order of operations per run, and why:

1. **Read the cursor.** `PollRepos.credential.get_by_account_email` gives
   the stored `last_history_id` for the account the injected `reader` was
   built for (`reader.account` — the reader is the one thing that knows
   which mailbox it reads). No cursor yet (first ever poll, or a `None`
   `last_history_id`) means "the fallback path decides `history_since`
   would have nothing to compare against, so go straight to it.
2. **Ask Gmail what's new.** `reader.history_since(history_id)` — Gmail's
   cheap "what changed since X" call. A stale/expired cursor answers with
   `GmailHistoryExpired` (raised by the real `GmailReader`, spec §5's
   documented ~week-old retention limit): fall back to
   `reader.recent_message_ids()` (a plain inbox scan) and mint a fresh
   cursor via `reader.current_history_id()`. Neither adapter exception
   class is imported here (that would violate the layering rule the
   architecture test pins) — they are recognised by name, the same
   duck-typing this project already leans on at its port boundaries.
3. **Per message id: dedupe before fetching.** `messages.gmail_message_id`
   carries a unique index (Task 1) and `MessageRepo.get_by_gmail_id`
   checks it BEFORE calling `reader.get_message(id)` — re-polling the same
   window is then free (no network call, not just "no duplicate row"),
   which is what makes the coarse fallback scan safe to use liberally.
4. **Fetch, then match the thread.** `reader.get_message(id)` gives the
   full message; `OutreachRepo.get_by_thread_id` maps its `gmail_thread_id`
   back to the outreach we sent it on. No match (the operator's own
   ordinary inbox mail, or anything else not ours) — `skipped`, never
   stored, never handed anywhere near the classifier.
5. **Bounce check by sender, before storing.** `mailer-daemon@` /
   `postmaster@` (by the address's LOCAL PART, exact, case-insensitive —
   never a substring of the whole header, so a founder legitimately named
   "Postmaster" in a display name is never caught by this) marks the
   outreach `BOUNCED` (a legal `SENT -> BOUNCED` transition per
   `app.domain.states.assert_transition`) and stores the row with
   `label="BOUNCE"` — pre-labelled precisely so Task 9's classifier never
   sees it. Anything else is stored as a plain inbound row, `label=None`.
6. **Commit per stored/bounced message** (global constraint): a crash
   mid-run keeps whatever was already written; the next poll re-asks Gmail
   for the same window (courtesy of the not-yet-persisted cursor) and the
   unique index makes re-storing any of them a no-op.
7. **Persist the new cursor LAST**, after every message in this run's
   window is committed. If the process dies before this line, the next
   poll re-reads the same window — a few wasted `get_message` calls at
   worst, never a skipped reply, because nothing here trusts an
   in-progress run's cursor before it is durably the new "since" point.

`PollReplies.run()` catches a Gmail transport failure (a `GmailError` from
either `history_since`/`recent_message_ids`/`get_message`, or from the
reader's own token refresh) and ends the run cleanly with whatever partial
`PollResult` has accumulated so far — the reader deliberately does not
retry a 401 in-request (Task 7's own docstring), so the next *scheduled*
poll refreshes naturally instead of this run looping or crashing the
caller (e.g. the job runner)."""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.domain.states import IllegalTransition, OutreachState, assert_transition

logger = logging.getLogger(__name__)

_BOUNCE_LOCAL_PARTS = {"mailer-daemon", "postmaster"}


@dataclass
class PollResult:
    fetched: int = 0   # messages actually pulled full content for (post-dedupe)
    stored: int = 0    # ordinary inbound rows written
    bounced: int = 0   # bounce rows written (outreach -> BOUNCED)
    skipped: int = 0   # mail on a thread we didn't send — never ours


class _CredentialRepo(Protocol):
    def get_by_account_email(self, account_email: str): ...
    def set_history_id(self, account_email: str, history_id: str) -> None: ...


class _OutreachRepo(Protocol):
    def get_by_thread_id(self, thread_id: str): ...
    def upsert(self, **fields): ...


class _MessageRepo(Protocol):
    def get_by_gmail_id(self, gmail_message_id: str): ...
    def upsert(self, **fields): ...


class _Committable(Protocol):
    def commit(self) -> None: ...


@dataclass
class PollRepos:
    """What `PollReplies` needs. `founder` is carried for the same reason
    every other *Repos bundle in this codebase carries every repo the use
    case's family might plausibly touch (`SendRepos`, `DraftRepos`, ...),
    even though this use case's own body never reads through it —
    consistent shape across every application module."""

    outreach: _OutreachRepo
    message: _MessageRepo
    founder: object
    credential: _CredentialRepo
    session: _Committable


def _is_bounce(from_address: str) -> bool:
    """Bounce detection: match the LOCAL PART of the address, exactly and
    case-insensitively — never a substring of the whole header. A founder
    whose address happens to contain "postmaster" (e.g.
    `founder-postmaster@startup.com`) must never trip this; only an exact
    `mailer-daemon@...` / `postmaster@...` sender does."""
    local = (from_address or "").split("@", 1)[0].strip().lower()
    return local in _BOUNCE_LOCAL_PARTS


class PollReplies:
    def __init__(self, reader, repos: PollRepos, clock):
        self._reader = reader
        self._repos = repos
        self._clock = clock
        # The reader is the one thing that knows which mailbox it reads
        # (Task 7's `GmailReader` is constructed with a fixed `account`);
        # asking it rather than threading a separate constructor arg
        # through keeps this class's interface exactly `(reader, repos,
        # clock)`, matching the brief's Interfaces line.
        self._account_email = getattr(reader, "account", None)

    def run(self) -> PollResult:
        result = PollResult()
        try:
            self._run(result)
        except Exception as exc:  # noqa: BLE001 — recognised by name, see module docstring
            if type(exc).__name__ == "GmailError":
                logger.warning(
                    "poll_replies: gmail error this run (%s); ending cleanly, "
                    "the next scheduled poll will refresh and retry",
                    type(exc).__name__,
                )
                return result
            raise
        return result

    def _run(self, result: PollResult) -> None:
        credential = self._repos.credential.get_by_account_email(self._account_email)
        history_id = getattr(credential, "last_history_id", None)

        if history_id:
            try:
                ids, new_cursor = self._reader.history_since(history_id)
            except Exception as exc:  # noqa: BLE001 — see module docstring
                if type(exc).__name__ != "GmailHistoryExpired":
                    raise
                ids = self._reader.recent_message_ids()
                new_cursor = self._reader.current_history_id()
        else:
            ids = self._reader.recent_message_ids()
            new_cursor = self._reader.current_history_id()

        for message_id in ids:
            if self._repos.message.get_by_gmail_id(message_id) is not None:
                continue  # already stored — free re-poll, no fetch burned

            msg = self._reader.get_message(message_id)
            result.fetched += 1

            outreach = self._repos.outreach.get_by_thread_id(msg.gmail_thread_id)
            if outreach is None:
                result.skipped += 1  # not one of our threads — the operator's own mail
                continue

            if _is_bounce(msg.from_address):
                self._store_bounce(outreach, msg)
                result.bounced += 1
            else:
                self._store_inbound(outreach, msg)
                result.stored += 1
            self._repos.session.commit()

        # Persisted LAST (module docstring point 7): a crash before this
        # line means the next poll re-reads this same window rather than
        # skipping it — harmless, since every message above is deduped by
        # `gmail_message_id` on the way back in.
        self._repos.credential.set_history_id(self._account_email, new_cursor)
        self._repos.session.commit()

    def _store_inbound(self, outreach, msg) -> None:
        self._repos.message.upsert(
            outreach_id=outreach.id,
            sequence_step=outreach.sequence_step,
            direction="inbound",
            subject=msg.subject,
            body=msg.body_text,
            gmail_message_id=msg.gmail_message_id,
            gmail_thread_id=msg.gmail_thread_id,
            received_at=msg.received_at,
        )

    def _store_bounce(self, outreach, msg) -> None:
        self._repos.message.upsert(
            outreach_id=outreach.id,
            sequence_step=outreach.sequence_step,
            direction="inbound",
            subject=msg.subject,
            body=msg.body_text,
            gmail_message_id=msg.gmail_message_id,
            gmail_thread_id=msg.gmail_thread_id,
            received_at=msg.received_at,
            label="BOUNCE",
        )
        try:
            assert_transition(OutreachState(outreach.state), OutreachState.BOUNCED)
        except IllegalTransition:
            # Outreach isn't in a state a bounce can legally close out from
            # (e.g. already REPLIED/CLOSED/BOUNCED) — the row is still
            # recorded and pre-labelled so it can never reach the
            # classifier, but the state itself is left alone rather than
            # raising and losing the rest of this run.
            logger.warning(
                "poll_replies: bounce on outreach %s but %s -> BOUNCED is illegal; "
                "message stored, state left unchanged",
                outreach.id, outreach.state,
            )
            return
        self._repos.outreach.upsert(founder_id=outreach.founder_id, state=str(OutreachState.BOUNCED))
