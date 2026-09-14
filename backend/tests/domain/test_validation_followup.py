"""A follow-up earns less attention than the first email did, not more
(spec §6). The rule is deterministic so the model cannot argue with it.

PF-3 (task 12 pre-flight ruling): the shorter-than-previous rule itself
already exists in `validate_style` (see
`tests/domain/test_validation_style.py::test_followup_must_be_shorter_than_previous`)
and is NOT reimplemented here. This file's own scope is what PF-3 found
genuinely missing: `FOLLOW_UP_WORD_TARGETS` and the near-restatement
rejection (a follow-up that says the same thing as the first email, even
if it happens to use fewer words)."""
import pytest

from app.domain.entities import Draft
from app.domain.validation import FOLLOW_UP_WORD_TARGETS, validate_style

FIRST = ("Vahan routes blue-collar hiring over voice.\n\n"
         "I built a scheduler in Python that cut queue wait by half.\n\n"
         "Would you take a software engineering intern?")


def _draft(body, step=1):
    return Draft(subject="Vahan intern follow up", body=body,
                 research_source_type="news", research_source_id=1, sequence_step=step)


def test_a_follow_up_longer_than_the_first_email_is_rejected():
    # PF-3: passes immediately against the pre-existing shorter-than-previous
    # rule in `validate_style` — kept as a regression pin, not new coverage.
    longer = FIRST + "\n\nAlso I have more to say about all of this at length."
    errs = validate_style(_draft(longer), previous_body=FIRST)
    assert any("shorter" in e for e in errs)


def test_a_shorter_follow_up_passes():
    shorter = "Following up on the note about voice routing.\n\nStill keen to intern with you."
    assert validate_style(_draft(shorter), previous_body=FIRST) == []


def test_the_step_word_targets_shrink():
    assert FOLLOW_UP_WORD_TARGETS[1] > FOLLOW_UP_WORD_TARGETS[2]


def test_a_follow_up_that_restates_the_first_email_is_rejected():
    errs = validate_style(_draft(FIRST), previous_body=FIRST)
    assert errs


def test_a_first_email_is_not_subject_to_the_shrink_rule():
    assert validate_style(_draft(FIRST, step=0), previous_body=None) == []


# --- Beyond the brief: a mutation-killing test for near-restatement --------
#
# `test_a_follow_up_that_restates_the_first_email_is_rejected` above passes
# even without any new code, because an IDENTICAL body is never shorter than
# itself and the pre-existing shrink rule alone rejects it (PF-3). That
# leaves the near-restatement rule itself unguarded: delete it and every
# brief test still passes. This test closes that hole with a follow-up that
# IS shorter (word count) than FIRST, word-for-word, but says nothing new —
# it must still be rejected, and only the restatement rule can catch it.
def test_a_follow_up_that_trims_but_still_restates_the_first_email_is_rejected():
    trimmed = ("Vahan routes blue-collar hiring over voice.\n\n"
               "I built a scheduler in Python.\n\n"
               "Would you take a software engineering intern?")
    assert len(trimmed.split()) < len(FIRST.split())  # genuinely shorter...
    errs = validate_style(_draft(trimmed), previous_body=FIRST)
    assert any("restat" in e for e in errs)  # ...yet still rejected, on its own


def test_a_shorter_reworded_follow_up_does_not_trip_the_restatement_rule():
    # Guards the restatement rule from being trigger-happy: a follow-up that
    # is both shorter AND actually different content must still pass.
    reworded = "Following up on the note about voice routing.\n\nStill keen to intern with you."
    errs = validate_style(_draft(reworded), previous_body=FIRST)
    assert not any("restat" in e for e in errs)


# --- Fix round 1: pin FOLLOW_UP_RESTATEMENT_OVERLAP's actual value (0.6) ----
#
# Review finding: `test_a_follow_up_that_trims_but_still_restates_...` above
# uses a body whose overlap ratio is effectively 1.0 (drops one clause), so
# it only pins the threshold somewhere above "always true" — moving
# `FOLLOW_UP_RESTATEMENT_OVERLAP` from 0.6 to 0.85 still passes the whole
# suite. This body's overlap with FIRST is 9/11 terms = 0.818..., strictly
# between 0.6 and 0.85: it must be rejected at the real 0.6 threshold and
# would wrongly pass at 0.85.
def test_a_follow_up_with_moderate_overlap_is_still_rejected():
    moderate = ("Quick note on Vahan routes hiring over voice with a scheduler in Python.\n\n"
                "Would you take an intern?")
    assert len(moderate.split()) < len(FIRST.split())  # genuinely shorter
    errs = validate_style(_draft(moderate), previous_body=FIRST)
    assert any("restat" in e for e in errs)


# --- Fix round 1 (ruling T12-1): D29 also covers the sender's own earlier
# email, not just a research source. Each phrase gets its own test so a
# regression in any one of them (not just the whole list) fails visibly.
@pytest.mark.parametrize("phrase", [
    "my last email", "my previous email", "my earlier email", "my previous note",
    "as I mentioned", "in case you missed", "following up on my",
])
def test_self_referencing_the_earlier_email_is_rejected(phrase):
    body = f"Vahan routes blue-collar hiring over voice - {phrase} last week.\n\nWould you take an intern?"
    errs = validate_style(_draft(body), previous_body=None)
    assert any("banned phrase" in e and phrase.lower() in e for e in errs)


def test_natural_continuation_without_naming_the_channel_is_not_banned():
    # "following up on ..." (no "my") is the prompt's own safe example —
    # must not collide with the "following up on my" ban.
    body = "Following up on the note about voice routing.\n\nWould you take an intern?"
    errs = validate_style(_draft(body), previous_body=None)
    assert not any("banned phrase" in e for e in errs)
