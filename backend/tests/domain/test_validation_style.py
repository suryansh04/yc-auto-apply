# backend/tests/domain/test_validation_style.py
import pytest

from app.domain.entities import Draft
from app.domain.validation import (
    AI_TELL_PHRASES,
    MAX_PARAGRAPHS,
    MAX_SENTENCE_WORDS,
    MAX_SENTENCES_PER_PARAGRAPH,
    MAX_SUBJECT_WORDS,
    MAX_WORDS,
    validate_style,
)

GOOD_BODY = (
    "You're matching blue-collar workers to jobs over voice, in a dozen languages. "
    "That routing problem is the part I keep thinking about.\n\n"
    "I'm a final-year CS student. I built a scheduler that cut queue wait by half. "
    "Python and Postgres mostly.\n\n"
    "Would you take an intern this summer? Happy to send code."
)

def draft(body=GOOD_BODY, subject="Backend intern at Vahan", step=0):
    return Draft(subject=subject, body=body, research_source_type="news",
                 research_source_id=1, sequence_step=step)

def test_the_reference_example_passes():
    assert validate_style(draft()) == []

def test_rejects_a_body_over_the_word_cap():
    body = "word " * (MAX_WORDS + 1) + "intern"
    assert any(f"{MAX_WORDS} words" in e for e in validate_style(draft(body=body)))

def test_rejects_a_sentence_over_the_sentence_cap():
    long_sentence = " ".join(["word"] * (MAX_SENTENCE_WORDS + 1)) + " intern."
    assert any(f"{MAX_SENTENCE_WORDS} words" in e for e in validate_style(draft(body=long_sentence)))

def test_rejects_more_paragraphs_than_the_cap():
    body = "\n\n".join(["An intern ask here."] * (MAX_PARAGRAPHS + 1))
    assert any("paragraph" in e for e in validate_style(draft(body=body)))

def test_rejects_semicolons():
    body = "You build voice AI; I want an internship."
    assert any("semicolon" in e for e in validate_style(draft(body=body)))

def test_rejects_more_than_half_sentences_starting_with_I():
    body = "I built a thing. I like this. I want an internship. You do voice."
    assert any('"I"' in e for e in validate_style(draft(body=body)))

def test_rejects_banned_phrases():
    body = "I hope this email finds you well. Would you take an intern?"
    assert any("banned phrase" in e for e in validate_style(draft(body=body)))

def test_rejects_seniority_claims():
    body = "I have three years of experience. Would you take an intern?"
    assert any("seniority" in e for e in validate_style(draft(body=body)))

def test_rejects_location_mentions():
    body = "I can work remote. Would you take an intern?"
    assert any("location" in e for e in validate_style(draft(body=body)))

def test_rejects_missing_internship_ask():
    body = "You build voice AI. I built a scheduler. Would you chat?"
    assert any("internship ask" in e for e in validate_style(draft(body=body)))

def test_rejects_bad_subject_length():
    assert any("subject" in e for e in validate_style(draft(subject="Hi")))
    too_long = " ".join(["word"] * (MAX_SUBJECT_WORDS + 1))
    assert any("subject" in e for e in validate_style(draft(subject=too_long)))


def test_accepts_the_operators_seven_word_subject_style():
    # "SDE intern who's been poking around RoboGPT" - the operator's own
    # template (2026-09-17); the old 6-word cap rejected it.
    assert validate_style(
        draft(subject="SDE intern who's been poking around RoboGPT")) == []

def test_followup_must_be_shorter_than_previous():
    errs = validate_style(draft(step=1), previous_body="short intern ask")
    assert any("shorter" in e for e in errs)


# --- Fix round 1 coverage (I2, I4, and cheap minors) ---

def test_rejects_a_paragraph_with_more_sentences_than_the_cap():
    # One paragraph, cap+1 sentences, no paragraph break: isolates the
    # per-paragraph sentence-count rule from the paragraph-COUNT rule, which
    # the paragraph-count test (one-sentence paragraphs) never exercises.
    body = (" ".join(["You build voice AI for blue-collar workers."] * MAX_SENTENCES_PER_PARAGRAPH)
            + " Would you take an intern?")
    errs = validate_style(draft(body=body))
    assert f"a paragraph exceeds {MAX_SENTENCES_PER_PARAGRAPH} sentences" in errs


def test_rejects_more_than_two_em_dashes():
    body = "You build voice AI — nice — really — impressive. Would you take an intern?"
    assert any("em dash" in e for e in validate_style(draft(body=body)))


def test_dash_count_includes_en_dash_and_double_hyphen():
    body = ("You build voice AI – nice -- really — impressive today. "
            "Would you take an intern?")
    assert any("em dash" in e for e in validate_style(draft(body=body)))


def test_rejects_shouting_subject_with_multiple_allcaps_words():
    errs = validate_style(draft("Would you take an intern? Thanks.",
                                 subject="URGENT INTERN REQUEST NOW"))
    assert any("all-caps" in e for e in errs)


def test_accepts_a_single_acronym_company_name_in_subject():
    # D-something: a legitimate acronym company name must not read as
    # shouting. One all-caps token alone no longer trips the rule.
    # Two paragraphs so this subject-focused test is not also asserting the
    # MIN_PARAGRAPHS rule (live finding 2026-09-13).
    body = "Thanks a lot friend.\n\nWould you take an intern?"
    assert validate_style(draft(body, subject="Backend intern at ZOKO")) == []
    assert validate_style(draft(body, subject="Backend intern at Coulomb AI")) == []


def test_rejects_subject_with_emoji():
    errs = validate_style(draft("Would you take an intern? Thanks.",
                                 subject="Backend intern \U0001F680 today"))
    assert any("emoji" in e for e in errs)


def test_rejects_reaching_out_banned_phrase():
    body = "Reaching out because I think this is exciting. Would you take an intern?"
    assert any("banned phrase" in e for e in validate_style(draft(body=body)))


def test_location_phrase_does_not_false_positive_on_partial_word():
    body = ("I built a rule-based inference engine for cloud-based interfaces. "
            "Would you take an intern?")
    errs = validate_style(draft(body=body))
    assert not any("location" in e for e in errs)


# --- Live finding 2026-09-16: "looks very AI generated" -------------------
# The operator's chosen tone example (curious and specific: a real question
# about the mechanism, the student's one project and why it connects, a
# plain ask). Restored to the operator's own 31-word opening sentence after
# the 2026-09-17 decision to raise the caps - an email that has room to
# breathe reads less machine-cut than three clipped sentences.

OPERATOR_EXAMPLE = (
    "I read your launch post about turning a new pick-and-place task into a "
    "sentence instead of a reprogramming job. That stuck with me because I "
    "spent a chunk of my last project wiring four monitoring tools together, "
    "and the painful part was never the APIs - it was making sure the same "
    "incident never got processed twice.\n\n"
    "I can't try a robot arm from my desk, but the choice I noticed is that "
    "RoboGPT takes the prompt and the vision model separately rather than "
    "fusing them.\n\n"
    "I'm a third-year B.Tech Computer Science student at Manipal University "
    "Jaipur. I built Lerno.ai, which turns a topic into an animated video "
    "lesson, and got generation down from about 8 minutes to under 3. That "
    "taught me how much of an AI product is deciding when the model is sure "
    "enough to act without a human, which is the same question your planner "
    "has to answer.\n\n"
    "I'd like to join Orangewood as an SDE intern and work on that planning "
    "layer. It's a problem I've actually hit, on a much smaller scale.\n\n"
    "Resume is attached. Would you be open to a quick 15-minute chat?"
)

def test_the_operator_example_passes_style():
    assert validate_style(draft(body=OPERATOR_EXAMPLE, subject="Intern question about RoboGPT")) == []


def test_the_operator_example_is_the_one_in_the_drafter_prompt():
    # The prompt's tone example and the validator's reference example are
    # the same text - if one is edited the other must follow, or the prompt
    # would be asking for a voice the validator rejects.
    import re
    from app.adapters.gemini import _DRAFTER_SYSTEM
    squash = lambda t: re.sub(r"\s+", " ", t)
    assert squash(OPERATOR_EXAMPLE) in squash(_DRAFTER_SYSTEM)


@pytest.mark.parametrize("phrase", AI_TELL_PHRASES)
def test_rejects_each_ai_tell_phrase(phrase):
    body = f"You build voice AI and it {phrase} me. Would you take an intern?"
    errs = validate_style(draft(body=body))
    assert any(f"banned phrase: {phrase!r}" in e for e in errs)


def test_ai_tells_never_reject_a_company_name():
    body = ("Incredible Health matches nurses to hospitals over one app. "
            "Where does the matching break first, the hospitals or the nurses?\n\n"
            "I'm a student. I built a scheduler that cut queue wait by half, resume attached.\n\n"
            "Would Incredible Health take an intern this year?")
    assert not [e for e in validate_style(draft(body=body)) if "banned phrase" in e]


def test_the_ai_tell_list_covers_the_rejected_draft():
    rejected = (
        "Orangewood's RoboGPT lets anyone program a robotic arm with a text prompt. "
        "That natural language layer feels like the exact interface small shops "
        "need to actually adopt automation.\n\n"
        "I cut manual grading time by 70% on WaveWatch by building an AI pipeline.\n\n"
        "Would Orangewood take on a software engineering intern?"
    )
    assert any("banned phrase" in e for e in validate_style(draft(body=rejected)))


# --- Live findings 2026-09-17 (the connection template, on real data) -----

@pytest.mark.parametrize("claim", [
    "I signed up and tried sending a text prompt to move an object.",
    "I tried it last week and the setup was one step.",
    "I have used it on a side project.",
    "I created an account to see the onboarding.",
])
def test_rejects_claiming_to_have_used_the_product(claim):
    # The model wrote the first of these to a ROBOT ARM company. A founder
    # can check their own signup list; the claim is banned outright.
    body = f"You build robot arms.\n\n{claim}\n\nWould you take an intern?"
    assert any("banned phrase" in e for e in validate_style(draft(body=body)))


def test_rejects_our_internal_source_kind_names():
    # "In your long description you said ..." - that is our database
    # schema talking, not a person who read what they wrote.
    body = ("In your long description you said you do procurement.\n\n"
            "Would you take an intern?")
    assert any("banned phrase" in e for e in validate_style(draft(body=body)))


def test_the_company_name_acronym_is_not_shouting():
    # "SDE internship at Bulk MRO" was rejected as shouting: two all-caps
    # tokens, both legitimate (the role asked for, the company's own name).
    errs = validate_style(draft("Would you take an intern? Thanks."),
                          company_name="Bulk MRO Industrial Supply")
    assert not any("all-caps" in e for e in errs)


def test_genuine_shouting_is_still_rejected():
    errs = validate_style(draft("Would you take an intern? Thanks.",
                                subject="URGENT HIRING REQUEST NOW"),
                          company_name="Bulk MRO Industrial Supply")
    assert any("all-caps" in e for e in errs)
