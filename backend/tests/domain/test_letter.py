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
    assert "Smart India Hackathon 2025, competing against 68,000+ teams" in body
    assert "CodeClash 2.0 at the Google office, competing against 1,000+ teams" in body


def test_the_company_paragraph_sits_between_what_he_built_and_the_bridge():
    # 2026-09-23: the "Now Here's the Important Part:" hinge is gone; the
    # company paragraph is now anchored by BUILT before it and BRIDGE
    # after it, and BRIDGE's "That's the kind of problem" only reads
    # correctly in that position.
    body = letter.assemble_body("Zave", INSIGHT)
    assert body.index(letter.BUILT) < body.index(INSIGHT[0]) < body.index(letter.BRIDGE)
    # ...and all of it before the ask, so the letter still turns to the
    # founder and then asks, rather than asking and then explaining why.
    assert body.index(INSIGHT[0]) < body.index(letter.ASK.format(company="Zave"))


def test_the_ask_names_the_company():
    assert "do that at Zave." in letter.assemble_body("Zave", INSIGHT)


def test_no_fixed_block_makes_a_claim_about_the_company():
    # 2026-09-23: the ask used to open "I can see that you have great
    # users and growth" - the one fixed line that asserted something
    # about the founder, sent to pre-launch companies as readily as to
    # anyone else.
    fixed = " ".join(letter.fixed_blocks("Zave")).lower()
    for claim in ("great users", "growth", "impressive", "traction"):
        assert claim not in fixed


def test_the_letter_is_plain_text():
    # D10. The example the operator supplied used markdown bold; a bold
    # marker reaches a founder as a literal asterisk.
    body = letter.assemble_body("Zave", INSIGHT)
    assert "*" not in body
    assert "<" not in body and ">" not in body
    # Smart quotes and em dashes are the other things that arrive as
    # punctuation debris in a plain-text client.
    for char in ("‘", "’", "“", "”", "—"):
        assert char not in body


def test_the_generic_line_names_the_company_and_claims_nothing_about_it():
    # Operator request (2026-09-23): a fallback for companies where no
    # good specific line can be found. The alternative to a generic line
    # is not a better specific one - it is a fabricated one.
    line = letter.generic_insight("Zave")
    assert "Zave" in line
    for claim in ("users", "growth", "impressive", "traction", "revenue", "customers"):
        assert claim not in line.lower()


def test_the_generic_line_can_replace_a_specific_one_in_place():
    body = letter.assemble_body("Zave", INSIGHT)
    swapped = letter.with_insight(body, [letter.generic_insight("Zave")])
    assert INSIGHT[0] not in swapped
    assert letter.generic_insight("Zave") in swapped
    # Everything else survives, verbatim.
    for block in letter.fixed_blocks("Zave"):
        assert block in swapped


def test_an_operator_edit_elsewhere_survives_the_swap():
    body = letter.assemble_body("Zave", INSIGHT).replace(
        "I've attached my resume.", "My resume is attached."
    )
    swapped = letter.with_insight(body, ["A different line entirely."])
    assert "My resume is attached." in swapped
    assert "A different line entirely." in swapped


def test_a_letter_without_the_anchors_is_left_alone():
    # A letter drafted before this module was rewritten, or one the
    # operator restructured by hand. Guessing which paragraph is "the
    # company one" would put the rewrite in the wrong place.
    assert letter.insight_region("Some other letter entirely.") is None
    assert letter.with_insight("Some other letter entirely.", ["new"]) is None


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
    # top. The blocks grew on 2026-09-23 (193 -> 237 words) and the cap
    # deliberately did NOT grow with them: the opener now promises 45
    # seconds, so a longer letter would be a promise the letter breaks.
    assert MAX_WORDS - fixed >= 140


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
