"""Task 9 (Phase 2, spec §5): the six reply labels and what each one means
for an outreach. Pure domain — no I/O, no imports from `app.application` or
`app.adapters` (enforced by `tests/test_architecture.py`).

Four labels are HUMAN replies — a person, not a bot, actually decided
something: `INTERESTED`, `REJECTED`, `REFERRAL`, `UNCLEAR`. Each of these
ends the outreach's follow-up sequence (`app.application.classify_reply`
moves the outreach to `REPLIED` and clears `next_action_at` through
`assert_transition`).

Two labels are MACHINE replies: `AUTO_REPLY`, `OUT_OF_OFFICE`. These are
recorded — visible to the operator, never silently dropped — but must
NEVER end an outreach. An autoresponder firing back the instant a cold
email lands must not be mistaken for a human decision; this is the single
most important rule this task implements (spec §5).

`UNCLEAR` is deliberately a HUMAN label, not a third bucket: a reply that
is too ambiguous to classify with confidence is still evidence a human
read the email and wrote something back, so it still ends the automated
follow-up sequence — the alternative (leaving `next_action_at` set) risks
a founder who already replied getting a robotic follow-up nudge.
`app.application.classify_reply` also routes any label the model returns
that is NOT one of the six below (a hallucinated label, or one with odd
casing/whitespace) to `UNCLEAR` before it ever reaches this function —
`cancels_sequence` itself only recognises the four exact human strings, so
an unrecognised value is never accidentally trusted to end an outreach.
"""

LABELS = ("INTERESTED", "REJECTED", "REFERRAL", "AUTO_REPLY", "OUT_OF_OFFICE", "UNCLEAR")

_HUMAN_LABELS = frozenset({"INTERESTED", "REJECTED", "REFERRAL", "UNCLEAR"})


def cancels_sequence(label: str) -> bool:
    """True for the four human labels; False for the two machine labels
    and for anything else — an unrecognised string is never trusted to end
    an outreach, only an exact match on one of the four."""
    return label in _HUMAN_LABELS
