"""The operator's assembled letter (app/domain/letter.py).

These pin the reason the module exists: the facts about the sender are
his own words, pasted in, and the model can no longer touch them.
"""
import pytest

from app.domain import letter
from app.domain.compose import compose_wire_text
from app.domain.validation import MAX_PARAGRAPHS, MAX_WORDS, validate_style


class _Draft:
    def __init__(self, subject, body, sequence_step=0):
        self.subject = subject
        self.body = body
        self.sequence_step = sequence_step


INSIGHT = [
    "You didn't try to build another shopping app. You built a layer that "
    "sits on top of where people already shop.",
]


def test_the_senders_own_facts_appear_verbatim():
    """The whole point: three live drafts were rejected for garbling these
    (a Better metric attributed to WaveWatch). Nothing regenerates them."""
    body = letter.assemble_body("Zave", INSIGHT)
    for block in (letter.OPENER, letter.INTRO, letter.BACKGROUND, letter.BUILT):
        assert block in body
    assert "Smart India Hackathon 2025 out of 68,000+ teams" in body
    assert "Codeclash 2.0 at the Google office out of 1,000+ teams" in body


def test_the_company_paragraph_sits_under_the_section_break():
    body = letter.assemble_body("Zave", INSIGHT)
    assert body.index(letter.SECTION_BREAK) < body.index(INSIGHT[0])
    # ...and before the ask, so the letter still turns to the founder and
    # then asks, rather than asking and then explaining why.
    assert body.index(INSIGHT[0]) < body.index(letter.ASK)


def test_the_close_names_the_company():
    assert "building Zave!" in letter.assemble_body("Zave", INSIGHT)


def test_empty_model_paragraphs_do_not_change_the_letters_shape():
    """A model that pads its array with blanks must not open a double
    blank line in the middle of the letter."""
    body = letter.assemble_body("Zave", ["", "   ", INSIGHT[0], ""])
    assert "\n\n\n" not in body
    assert body.count(INSIGHT[0]) == 1


def test_the_stub_is_only_added_when_asked_for():
    assert not letter.contains_stub(letter.assemble_body("Zave", INSIGHT))
    with_stub = letter.assemble_body("Zave", INSIGHT, include_user_fit_stub=True)
    assert letter.contains_stub(with_stub)


def test_contains_stub_is_what_send_checks():
    """`send_approved` refuses a body that still has the marker. An
    operator who filled the paragraph in - or deleted it - passes."""
    stubbed = letter.assemble_body("Zave", INSIGHT, include_user_fit_stub=True)
    filled = stubbed.replace(letter.USER_FIT_STUB, "I use Zave every week on Myntra.")
    assert letter.contains_stub(stubbed)
    assert not letter.contains_stub(filled)
    assert not letter.contains_stub(stubbed.replace(letter.USER_FIT_STUB + "\n\n", ""))


def test_the_word_cap_leaves_room_for_the_model_and_the_operator():
    """A cap below what the fixed blocks need would reject every draft at
    run time with no obvious cause. Fail here instead."""
    fixed = letter.fixed_word_count("Zave")
    assert fixed < MAX_WORDS, "the fixed blocks alone exceed the word cap"
    # Room for two company paragraphs and the operator's own paragraph on
    # top - roughly 150 words between them.
    assert MAX_WORDS - fixed >= 150


def test_a_fully_assembled_letter_passes_the_style_validator():
    """The end the operator actually cares about: the shape he asked for
    is not rejected by our own rules."""
    body = letter.assemble_body("Zave", INSIGHT, include_user_fit_stub=True)
    errors = validate_style(_Draft("Zave internship question", body), None, "Zave")
    assert errors == []


def test_the_assembled_letter_stays_inside_the_paragraph_cap():
    body = letter.assemble_body(
        "Zave", ["one paragraph here", "and a second one"], include_user_fit_stub=True
    )
    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    assert len(paragraphs) <= MAX_PARAGRAPHS


def test_the_wire_text_is_the_letter_plus_a_greeting_and_a_signature():
    # 2026-09-23: and nothing after the signature - the opt-out line was
    # deleted at the operator's instruction.
    body = letter.assemble_body("Zave", INSIGHT)
    wire = compose_wire_text("Hiren", body, "Thanks for reading\nSuryansh")
    assert wire.startswith("Hi Hiren,\n\n" + letter.OPENER)
    assert wire.endswith("Thanks for reading\nSuryansh")


@pytest.mark.parametrize("phrase", ["passionate about", "genuinely"])
def test_the_operators_own_blocks_are_not_policed_for_phrasing(phrase):
    """The phrase bans police the model's habits. A block the operator
    wrote is exempt, because the body it is removed from is what the model
    actually produced."""
    from app.domain.validation import _generated_region

    body = letter.assemble_body("Zave", [f"They are {phrase} the problem."])
    region = _generated_region(body, "Zave")
    assert letter.OPENER not in region
    assert letter.BUILT not in region
    assert phrase in region  # the model's own paragraph IS still checked
