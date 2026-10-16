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
    "While looking at Zave, I noticed you sit on top of the shopping apps "
    "people already use. Keeping that many integrations working is the kind "
    "of backend work I enjoyed most at Better.",
    "The other thing I got curious about is price tracking. How do you know "
    "when a price change is real? I'd love to learn how your team approaches that.",
]


def _old_letter(company="Zave", paragraph="An older company paragraph."):
    """A letter drafted from the previous template (2026-09-23)."""
    return "\n\n".join([
        letter.OLD_OPENER, letter.OLD_INTRO, letter.OLD_BACKGROUND, letter.OLD_BUILT,
        paragraph, letter.OLD_ASK.format(company=company), letter.OLD_CLOSE,
    ])


def test_the_senders_own_facts_appear_verbatim():
    """The whole point: three live drafts were rejected for garbling these
    (a Better metric attributed to WaveWatch). Nothing regenerates them."""
    body = letter.assemble_body("Zave", INSIGHT)
    for block in (letter.INTRO, letter.BETTER, letter.OUTSIDE):
        assert block in body
    assert "final-year CS (Data Science) student at Manipal University Jaipur" in body
    assert "Won Smart India Hackathon 2025 against 68,000+ teams" in body
    assert "cut repeated GitHub token requests by 98% with caching" in body


def test_the_template_is_the_operators_wareiq_letter():
    """Operator's researched template (2026-09-25): the tie-in continues
    the Better paragraph, the curious paragraph stands alone, then his
    projects and the ask."""
    body = letter.assemble_body("WareIQ", INSIGHT)
    paragraphs = body.split("\n\n")
    assert paragraphs[0] == letter.INTRO
    assert paragraphs[1] == f"{letter.BETTER} {INSIGHT[0]}"
    assert paragraphs[2] == INSIGHT[1]
    assert paragraphs[3] == "Outside work:"
    assert body.endswith("I'd love to intern at WareIQ this winter, remote or on-site. Resume attached.")


def test_the_company_part_sits_between_better_and_outside_work():
    body = letter.assemble_body("Zave", INSIGHT)
    assert letter.insight_region(body) == INSIGHT[0] + "\n\n" + INSIGHT[1]


def test_a_letter_from_the_previous_template_is_still_found():
    # Approved or sent before 2026-09-25: still reviewable, so the swap and
    # the hover card keep working on them.
    old = _old_letter()
    assert letter.insight_region(old) == "An older company paragraph."
    swapped = letter.with_insight(old, ["A different line entirely."])
    assert "A different line entirely." in swapped
    assert letter.OLD_BUILT in swapped


def test_the_ask_names_the_company():
    assert "intern at Zave this winter" in letter.assemble_body("Zave", INSIGHT)


# --- the founder line (2026-09-25) -------------------------------------------

LINE = "I also found your time building search at Flipkart really interesting."


def test_the_founder_line_ends_the_curious_paragraph():
    body = letter.assemble_body("Zave", INSIGHT, founder_line=LINE)
    assert f"{INSIGHT[1]} {LINE}\n\nOutside work:" in body


def test_a_new_founder_line_replaces_the_old_one_where_it_stands():
    body = letter.assemble_body("Zave", INSIGHT, founder_line=LINE)
    swapped = letter.with_founder_line(body, LINE, "A newer line about you.")
    assert LINE not in swapped
    assert f"{INSIGHT[1]} A newer line about you." in swapped


def test_a_letter_with_no_founder_line_gets_one_at_the_end_of_the_company_part():
    body = letter.assemble_body("Zave", INSIGHT)
    added = letter.with_founder_line(body, None, LINE)
    assert f"{INSIGHT[1]} {LINE}\n\nOutside work:" in added


def test_an_empty_founder_line_removes_it():
    body = letter.assemble_body("Zave", INSIGHT, founder_line=LINE)
    assert letter.with_founder_line(body, LINE, "") == letter.assemble_body("Zave", INSIGHT)


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
        "Resume attached.", "My resume is attached."
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
    # top. The blocks grew on 2026-09-23 (193 -> 237, then 226 once the
    # bridge line came out) and the cap
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
    assert wire.startswith("Hi Hiren,\n\n" + letter.INTRO)
    assert wire.endswith("Thanks for reading\nSuryansh")


@pytest.mark.parametrize("phrase", ["passionate about", "genuinely"])
def test_the_operators_own_blocks_are_not_policed_for_phrasing(phrase):
    """The phrase bans police the model's habits. A block the operator
    wrote is exempt, because the body it is removed from is what the model
    actually produced."""
    from app.domain.validation import _generated_region

    body = letter.assemble_body("Zave", [f"They are {phrase} the problem."])
    region = _generated_region(body, "Zave")
    assert letter.INTRO not in region
    assert letter.OUTSIDE not in region
    assert phrase in region  # the model's own paragraph IS still checked


def test_his_remote_or_on_site_offer_is_not_a_model_raising_location():
    """"remote" is banned for the MODEL; the ask is his own words."""
    from app.domain.validation import validate_style

    body = letter.assemble_body("Zave", INSIGHT)
    assert not [e for e in validate_style(_Draft("Would love to build at Zave", body), None, "Zave")
                if "location" in e]


# --- the subject line (operator request 2026-09-23) -----------------------


def test_the_subject_is_the_operators_line_not_the_models():
    # "change the subject to something like this 'Would love to build at
    # Loop'". Written down for the same reason every other block here is:
    # it is the same every time, so a model that rewrites it each morning
    # can only get it wrong.
    assert letter.subject("Loop Health") == "Would love to build at Loop Health"


def test_the_subject_fits_the_checks_that_judge_a_subject():
    # A subject the validator would reject is a draft that cannot be
    # approved, so the two have to agree by construction.
    from app.domain import validation

    for name in ("Zave", "Loop Health", "Orangewood Labs"):
        words = len(letter.subject(name).split())
        assert validation.MIN_SUBJECT_WORDS <= words <= validation.MAX_SUBJECT_WORDS



def test_outside_work_is_his_two_projects_as_bullets():
    items = letter.OUTSIDE.split("\n\n", 1)[1].split("\n")
    assert [i.split(":", 1)[0] for i in items] == ["• Lerno.ai", "• WaveWatch"]


def test_outside_work_is_formatted_and_reads_back_as_the_same_text():
    """Operator request (2026-09-25): bullets and proper formatting. The
    formatted version must turn back into exactly the plain text, or the
    checks and the anchors would read a different letter from the one sent."""
    from app.domain import rich_text

    body = letter.assemble_body("Zave", INSIGHT, founder_line=LINE)
    html = letter.formatted_html(body)
    assert "<ul><li><strong>Lerno.ai</strong>:" in html
    assert rich_text.sanitize(html) == html
    assert rich_text.to_text(html) == body


def test_every_detail_of_outside_work_is_from_his_resume():
    for fact in ("about 8 minutes to under 3", "20+ REST APIs", "68,000+ teams", "1,000+ teams"):
        assert fact in letter.OUTSIDE


def test_the_previous_templates_own_words_are_still_exempt():
    from app.domain.validation import _generated_region

    region = _generated_region(_old_letter(), "Zave")
    assert letter.OLD_BACKGROUND not in region
    assert "An older company paragraph." in region


def test_the_ask_is_his_even_when_the_company_has_a_legal_suffix():
    """Live, 2026-09-25: "Wifi Dabba, Inc." is asked for as "Wifi Dabba",
    and his "remote or on-site" was flagged as the model raising location."""
    from app.domain.validation import validate_style

    body = letter.assemble_body("Wifi Dabba", INSIGHT)
    errors = validate_style(_Draft("Would love to build at Wifi Dabba", body), None, "Wifi Dabba, Inc.")
    assert not [e for e in errors if "location" in e]


def test_calling_something_physical_backend_work_is_refused():
    """Live, 2026-09-25: crop-breeding chambers called "backend work"."""
    from types import SimpleNamespace

    from app.domain.validation import SourceRow, validate_grounding

    tie = ("While looking at UrbanKisaan, I noticed you build chambers for crop breeding. "
           "Keeping that physical infrastructure reliable is the kind of backend work I enjoyed most at Better.")
    body = letter.assemble_body("UrbanKisaan", [tie, INSIGHT[1]])
    draft = SimpleNamespace(body=body, subject="s", research_source_id=1, sequence_step=0)
    source = SourceRow("long_description", "chambers for crop breeding", None, None)
    errors = validate_grounding(draft, source, "UrbanKisaan", None, [])
    assert any("physical" in e for e in errors)


def test_a_founder_line_never_lands_after_the_placeholder():
    """Live, Inito 2026-09-25: the line went after ">>> YOUR TURN"."""
    body = letter.assemble_body("Inito", INSIGHT, include_user_fit_stub=True)
    added = letter.with_founder_line(body, None, LINE)
    assert f"{INSIGHT[1]} {LINE}\n\n{letter.USER_FIT_STUB}" in added
