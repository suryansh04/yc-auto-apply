"""Task 9 (Phase 2, spec §5): label every unclassified inbound reply and
decide what the label means for the outreach.

`ClassifyReply` is pure application logic — it depends only on an injected
`classifier` (the `ReplyClassifier` port), a bundle of repos
(`ClassifyRepos`) and a `clock` (D8), never on a concrete adapter, so this
module never imports `app.adapters` (enforced by `tests/test_architecture.py`).

Task 8's `PollReplies` deliberately stores every ordinary reply with
`label=None` and touches no outreach state (its own docstring says so
explicitly). This module is the other half: it finds those `label=None`
inbound rows, asks the classifier what each one means, and is the ONE
place in this codebase where a reply is allowed to move an outreach to
`REPLIED` and cancel its follow-up sequence.

Order of operations per run:

1. **List unclassified inbound rows.**
   `MessageRepo.list_unclassified_inbound()` — `direction='inbound' AND
   label IS NULL`. An already-labelled row (set by a prior run, OR by an
   operator's manual correction — Task 10's label-correction route) is
   never touched again: re-running this job must not overwrite a human's
   correction, so the classifier is never even called for it (this is
   what `fake_classifier.calls == []` in the test suite pins).
2. **Build enough context to judge intent, not just parse words.** "Sure,
   Friday works" is unclassifiable on its own — the classifier needs to
   see the outbound message the founder is replying to
   (`MessageRepo.get_outbound(outreach_id, sequence_step)`, the same
   `sequence_step` the reply itself was stored under by `PollReplies`)
   alongside the reply body itself.
3. **Classify. A failure is per-reply, never fatal to the run** (same
   shape as `DraftOutreach`'s per-founder drafter-failure handling): the
   row is left exactly as it was — still `label=None`, still visible,
   still eligible for classification on a later run — `result.errors` is
   incremented and the loop moves on to the next reply. Nothing here
   guesses a label for a call that failed.
4. **Normalise the label before it ever reaches the database or
   `cancels_sequence`.** A model can return anything: an unrecognised
   string, one of the six spec labels with stray whitespace or the wrong
   case, `None`. Anything that is not an EXACT, case-sensitive match for
   one of the six `app.domain.replies.LABELS` — no stripping, no
   upper-casing, no fuzzy mapping — becomes `UNCLEAR` — never stored raw,
   never crashes the run. This strictness is deliberate: a strict
   JSON-schema `enum` response should never legitimately come back as
   `"interested "` or `"Interested"`, so a value that close but not exact
   is a signal something upstream is already off-contract, not a typo
   safe to silently correct — `UNCLEAR` surfaces that to the operator
   instead of quietly guessing what the model "must have meant". `UNCLEAR`
   is itself a human label (see `app.domain.replies`'s module docstring),
   so this fallback is conservative in the safe direction either way: an
   ambiguous or malformed answer still closes out the sequence rather than
   leaving a founder who already replied on the hook for a robotic
   follow-up.
5. **Persist label/confidence/rationale/classified_at exactly as given.**
   Confidence is never clamped, rounded, or hidden below some threshold —
   spec §5's whole point is that a low-confidence label is still SHOWN to
   the operator, who can correct it (Task 10), not silently discarded.
6. **`cancels_sequence(label)` decides the outreach's fate — the ONLY
   place that boolean is consulted.** A human label transitions
   `SENT -> REPLIED` through `assert_transition` and clears
   `next_action_at` (spec §5: "follow-ups cancelled"). A machine label
   (`AUTO_REPLY`/`OUT_OF_OFFICE`) does *nothing else at all* — this is the
   single most important rule in this task: an autoresponder must never
   be able to end an outreach.
7. **An outreach already past `SENT`** (e.g. a second reply landing on a
   thread whose first reply already moved it to `REPLIED`) makes
   `SENT -> REPLIED` illegal a second time — mirrors
   `PollReplies._store_bounce`'s handling of the same shape of problem:
   the row is still labelled and counted, the state is left exactly as it
   was, and `IllegalTransition` never escapes this module.
8. **Commit per classified row** (same reasoning as `PollReplies`): a
   crash mid-run keeps whatever was already written, and the next run's
   `list_unclassified_inbound()` picks up exactly where this one left off
   — nothing here is re-processed or double-counted.

`apply_label` (Task 10, ruling T9-1) is the extraction of point 6 above
into its own method: the ONE place a label's classification (human vs.
machine, `cancels_sequence`) is turned into an outreach transition. It is
shared, unchanged, by two callers:

- `_classify_one` (this module, automatic): `message.label` is always
  `None` beforehand (only `list_unclassified_inbound()` rows reach it), so
  the only transition it can ever trigger is machine/none -> human
  (`SENT -> REPLIED`).
- `app.api.routers.thread`'s `POST /api/messages/{id}/label` (manual,
  Task 10): `message.label` is whatever it was before — a fresh
  classification OR an earlier correction — so a correction can go either
  direction:
  - human -> machine (T9-1, reachable ONLY from this path): the operator
    fixed a false positive (the classifier mistook, say, an
    out-of-office for `INTERESTED`, which had already cancelled the
    founder's follow-ups). `REPLIED -> SENT`, with `next_action_at`
    RESTORED — never left `None` — recomputed from the last outbound
    message's `sent_at` plus the follow-up interval for the next
    sequence step (`followup_day_1`/`followup_day_2`, read from the
    `settings` table via `ClassifyRepos.settings`, falling back to 5/12
    exactly as `SendApproved` does for `followup_day_1`).
  - machine -> human: cancels the sequence exactly as the automatic path
    does — `SENT -> REPLIED`.
  - human -> human / machine -> machine: only the label text changes; the
    outreach's state is left alone.

  An outreach that is not in the state the needed transition requires
  (e.g. a correction on a `BOUNCED` or already-corrected-twice outreach)
  is never corrupted: `assert_transition`'s refusal is swallowed exactly
  the way point 7 above already swallows it for the automatic path — the
  label itself still changes, the state does not.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from app.domain.replies import LABELS, cancels_sequence
from app.domain.states import IllegalTransition, OutreachState, assert_transition

logger = logging.getLogger(__name__)

_DEFAULT_FOLLOWUP_DAY_1 = 5   # spec §7 settings default — mirrors `send_approved.py`
_DEFAULT_FOLLOWUP_DAY_2 = 12  # spec §7 settings default


@dataclass
class ClassifyResult:
    classified: int = 0  # rows that got a persisted label this run (any of the six)
    replied: int = 0     # of those, human labels that actually moved the outreach to REPLIED
    ignored: int = 0     # machine labels, PLUS human labels whose transition was illegal (7 above)
    errors: int = 0      # classifier failures — row left unlabelled, visible, retryable


class _MessageRepo(Protocol):
    def list_unclassified_inbound(self): ...
    def get_outbound(self, outreach_id: int, sequence_step: int): ...
    def upsert(self, **fields): ...


class _OutreachRepo(Protocol):
    def get(self, id: int): ...
    def upsert(self, **fields): ...


class _Committable(Protocol):
    def commit(self) -> None: ...


class _SettingsRepo(Protocol):
    def get(self): ...


@dataclass
class ClassifyRepos:
    """What `ClassifyReply` needs — same two-repos-plus-session shape as
    `PollRepos` (Task 8), plus `settings` (Task 10, optional): the
    `followup_day_1`/`followup_day_2` source `apply_label`'s T9-1 restore
    path reads. Optional (defaults to `None`) so every existing call site
    that built a `ClassifyRepos` before Task 10 — this module's own
    automatic path never needs it — keeps working unchanged; a `None`
    settings repo (or a settings row with a `None` field) just falls back
    to the same defaults `SendApproved` uses for `followup_day_1`."""

    message: _MessageRepo
    outreach: _OutreachRepo
    session: _Committable
    settings: _SettingsRepo | None = None


def _normalise_label(raw_label) -> str:
    """Anything that is not an EXACT match — same case, no surrounding
    whitespace — for one of the six spec labels becomes `UNCLEAR`.
    Deliberately strict, not "strip + upper-case and hope": a model that
    returns `"interested "` or `"Interested"` has not clearly told us it
    means `INTERESTED` (strict JSON-schema mode with an `enum` should never
    let this happen for a well-behaved provider — a sloppy value is a sign
    something upstream is already misbehaving), so it is treated the same
    as a wholly unrecognised string like `"MAYBE"`: routed to `UNCLEAR`
    rather than silently coerced into a label the model didn't actually
    return."""
    return raw_label if isinstance(raw_label, str) and raw_label in LABELS else "UNCLEAR"


class ClassifyReply:
    def __init__(self, classifier, repos: ClassifyRepos, clock):
        self._classifier = classifier
        self._repos = repos
        self._clock = clock

    def run(self, limit: int | None = None) -> ClassifyResult:
        result = ClassifyResult()
        rows = self._repos.message.list_unclassified_inbound()
        if limit is not None:
            rows = rows[:limit]

        for row in rows:
            self._classify_one(row, result)

        return result

    def _classify_one(self, row, result: ClassifyResult) -> None:
        our_message = self._repos.message.get_outbound(row.outreach_id, row.sequence_step)
        context = {
            "our_message": getattr(our_message, "body", "") or "",
            "our_subject": getattr(our_message, "subject", "") or "",
            "reply_body": row.body or "",
        }

        try:
            raw = self._classifier.classify(context)
        except Exception as exc:  # noqa: BLE001 — any classifier failure is per-reply
            logger.warning(
                "classify_reply: classifier failed for message %s: %s",
                row.id, type(exc).__name__,
            )
            result.errors += 1
            return

        label = _normalise_label(raw.get("label") if isinstance(raw, dict) else None)
        confidence = raw.get("confidence") if isinstance(raw, dict) else None
        rationale = raw.get("rationale") if isinstance(raw, dict) else None

        transitioned = self.apply_label(
            row, label, confidence=confidence, rationale=rationale, classified_at=self._clock.now(),
        )
        result.classified += 1

        if cancels_sequence(label) and transitioned:
            result.replied += 1
        else:
            result.ignored += 1

        self._repos.session.commit()

    def apply_label(self, message, label: str, corrected: bool = False, **extra_fields) -> bool:
        """Persist `label` on `message` and apply everything that follows
        from it to the parent outreach — the ONE place a label's
        consequence is decided (point 6 of the module docstring), shared
        by `_classify_one` (automatic) and Task 10's manual correction
        route so the two can never drift.

        `corrected` (Task 10) is stamped on the message row's own
        `corrected` column, never reinterpreted here — it is what tells
        the thread view a label came from an operator's hand, not the
        classifier. `**extra_fields` (`confidence`/`rationale`/
        `classified_at`) are passed straight through to `upsert`, exactly
        as `_classify_one` always wrote them; the manual correction path
        passes none of these, so upsert's "only set what's given"
        semantics leave a message's original confidence/rationale/
        classified_at untouched by a later hand correction.

        Returns whether a state transition actually happened — `True` only
        when the outreach moved (`_mark_replied`/`_restore_sent` each
        return `False` when the needed `assert_transition` is illegal, or
        when the old and new label are in the same bucket and no
        transition was ever attempted)."""
        previous_label = message.label
        self._repos.message.upsert(id=message.id, label=label, corrected=corrected, **extra_fields)

        outreach = self._repos.outreach.get(message.outreach_id)
        if outreach is None:
            return False

        was_human = previous_label is not None and cancels_sequence(previous_label)
        is_human = cancels_sequence(label)

        if is_human and not was_human:
            return self._mark_replied(outreach)
        if was_human and not is_human:
            return self._restore_sent(outreach, message)
        return False

    def _mark_replied(self, outreach) -> bool:
        """`SENT -> REPLIED` through `assert_transition`, clearing
        `next_action_at` (spec §5: "follow-ups cancelled"). Returns False
        without raising when the outreach is already past `SENT` (point 7
        of the module docstring) — the caller counts that as `ignored`
        rather than `replied`, since nothing actually changed."""
        try:
            assert_transition(OutreachState(outreach.state), OutreachState.REPLIED)
        except IllegalTransition:
            logger.warning(
                "classify_reply: outreach %s is %s, not SENT — reply labelled, "
                "state left unchanged", outreach.id, outreach.state,
            )
            return False
        self._repos.outreach.upsert(
            founder_id=outreach.founder_id,
            state=str(OutreachState.REPLIED),
            next_action_at=None,
        )
        return True

    def _restore_sent(self, outreach, message) -> bool:
        """T9-1: the reverse of `_mark_replied` — reachable ONLY when an
        operator corrects a human label to a machine one (Task 10). The
        classifier will eventually mistake an out-of-office for
        `INTERESTED`, which cancels the founder's remaining follow-ups;
        this is what undoes that consequence, not just the label text.

        `REPLIED -> SENT` through `assert_transition`; an outreach that is
        not `REPLIED` (a second correction, or a terminal state such as
        `BOUNCED`) makes this illegal — swallowed exactly like
        `_mark_replied` does: the label itself still changed, the state is
        left exactly as it was, never corrupted.

        `next_action_at` is RESTORED, never left `None` — a null
        `next_action_at` here would silently drop the founder from the
        follow-up sequence forever, the exact failure T9-1 exists to
        prevent — recomputed as the last outbound message's `sent_at` plus
        the follow-up interval for the next sequence step."""
        try:
            assert_transition(OutreachState(outreach.state), OutreachState.SENT)
        except IllegalTransition:
            logger.warning(
                "classify_reply: outreach %s is %s, not REPLIED — label corrected, "
                "state left unchanged", outreach.id, outreach.state,
            )
            return False

        last_outbound = self._repos.message.get_outbound(outreach.id, outreach.sequence_step)
        self._repos.outreach.upsert(
            founder_id=outreach.founder_id,
            state=str(OutreachState.SENT),
            next_action_at=self._next_action_at(outreach, last_outbound),
        )
        return True

    def _next_action_at(self, outreach, last_outbound):
        """The last outbound message's `sent_at` plus the follow-up
        interval for the NEXT sequence step (`outreach.sequence_step + 1`
        — the step that hasn't gone out yet). `None` only when there is no
        outbound `sent_at` to anchor to — never for a missing interval:
        `_followup_interval_days` always returns one (see its docstring),
        so a restored row is never silently left un-schedulable."""
        if last_outbound is None or last_outbound.sent_at is None:
            return None
        interval_days = self._followup_interval_days(outreach.sequence_step + 1)
        return last_outbound.sent_at + timedelta(days=interval_days)

    def _followup_interval_days(self, next_step: int) -> int:
        """`followup_day_1`/`followup_day_2` from the `settings` table
        (`ClassifyRepos.settings`), falling back to the same defaults
        `SendApproved` uses for `followup_day_1` (spec §7) when there is no
        settings repo, no row, or the row's field is `None`.

        Task 11 review (ruling T11-1): `next_step == 1` uses
        `followup_day_1`; `next_step >= 2` uses `followup_day_2` —
        deliberately NOT `next_step == 2` only. Before Task 11,
        `next_step == 3` (a correction of a step-2 `SENT` row, i.e.
        `outreach.sequence_step == 2`) returned `None` here, which made
        `_restore_sent` leave `next_action_at = None` on the restored row.
        Once `ScheduleFollowUps.list_due` started excluding NULL
        `next_action_at` outright (Task 11, spec §6), that `None` became a
        real bug: the row would sit in `SENT` forever, never picked up,
        never closed. Mirrors `ScheduleFollowUps._interval_days`'s own
        choice to reuse `followup_day_2` as the step-2 "wait before close"
        interval, so the two never disagree on what a step-2 row's next
        due date is, regardless of which path (a normal send, or a
        REPLIED->SENT correction) produced it."""
        settings_row = self._repos.settings.get() if self._repos.settings is not None else None
        if next_step == 1:
            return getattr(settings_row, "followup_day_1", None) or _DEFAULT_FOLLOWUP_DAY_1
        return getattr(settings_row, "followup_day_2", None) or _DEFAULT_FOLLOWUP_DAY_2
