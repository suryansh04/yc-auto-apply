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
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.domain.replies import LABELS, cancels_sequence
from app.domain.states import IllegalTransition, OutreachState, assert_transition

logger = logging.getLogger(__name__)


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


@dataclass
class ClassifyRepos:
    """What `ClassifyReply` needs — same two-repos-plus-session shape as
    `PollRepos` (Task 8)."""

    message: _MessageRepo
    outreach: _OutreachRepo
    session: _Committable


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
        outreach = self._repos.outreach.get(row.outreach_id)
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

        self._repos.message.upsert(
            id=row.id,
            label=label,
            confidence=confidence,
            rationale=rationale,
            classified_at=self._clock.now(),
        )
        result.classified += 1

        if cancels_sequence(label) and outreach is not None and self._mark_replied(outreach):
            result.replied += 1
        else:
            result.ignored += 1

        self._repos.session.commit()

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
