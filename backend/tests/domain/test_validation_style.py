# backend/tests/domain/test_validation_style.py
from app.domain.entities import Draft
from app.domain.validation import validate_style

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

def test_rejects_body_over_120_words():
    assert any("120 words" in e for e in validate_style(draft(body="word " * 121 + "intern")))

def test_rejects_a_sentence_over_25_words():
    long_sentence = " ".join(["word"] * 26) + " intern."
    assert any("25 words" in e for e in validate_style(draft(body=long_sentence)))

def test_rejects_more_than_three_paragraphs():
    body = "\n\n".join(["An intern ask here."] * 4)
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
    assert any("subject" in e for e in validate_style(
        draft(subject="A rather long subject line about interning here")))

def test_followup_must_be_shorter_than_previous():
    errs = validate_style(draft(step=1), previous_body="short intern ask")
    assert any("shorter" in e for e in errs)
