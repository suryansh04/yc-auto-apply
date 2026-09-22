# backend/tests/domain/test_validate_draft.py
"""Coverage for validate_draft — the composed function Task 14 calls directly.
Added in fix round 1 (I3): before this file, nothing exercised validate_draft
itself, only its two halves separately."""
from app.domain.entities import Draft, ResumeClaim
from app.domain.validation import MAX_WORDS, validate_draft, validate_grounding, validate_style, SourceRow

NEWS = SourceRow(
    kind="news",
    text="OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI",
    publication="The Economic Times",
    intern_appropriate=None,
)
CLAIMS = [ResumeClaim(kind="project", text="built a scheduler in Python", source_span="...")]

# Passes both validate_style and validate_grounding: shares "blue-collar"/
# "voice" with NEWS.text, names "Vahan", three short paragraphs, well under
# the word and sentence caps.
GOOD_GROUNDED_BODY = (
    "You're matching blue-collar workers to jobs over voice, in a dozen languages, at Vahan. "
    "That routing problem is the part I keep thinking about.\n\n"
    "I'm a final-year CS student. I built a scheduler that cut queue wait by half. "
    "Python and Postgres mostly.\n\n"
    "Would you take an intern this summer? Happy to send code."
)


def d(body, subject="Backend intern at Vahan", step=0, stype="news", sid=1):
    return Draft(subject=subject, body=body, research_source_type=stype,
                 research_source_id=sid, sequence_step=step)


def test_fully_valid_draft_returns_no_errors():
    assert validate_draft(d(GOOD_GROUNDED_BODY), NEWS, "Vahan", "x", CLAIMS) == []


def test_validate_draft_surfaces_a_style_only_failure():
    # Grounded cleanly (shares a source term, names the company) but blows
    # the word cap: confirms validate_draft surfaces validate_style's errors
    # even when validate_grounding has nothing to say.
    body = "word " * (MAX_WORDS + 1) + "intern Vahan voice blue collar"
    draft = d(body)
    assert validate_grounding(draft, NEWS, "Vahan", "x", CLAIMS) == []
    errs = validate_draft(draft, NEWS, "Vahan", "x", CLAIMS)
    assert any(f"{MAX_WORDS} words" in e for e in errs)


def test_validate_draft_surfaces_a_grounding_only_failure():
    # Style-clean (identical shape to the Task 6 reference example) but
    # never names the company: confirms validate_draft surfaces
    # validate_grounding's errors even when validate_style has nothing to say.
    body = (
        "You're matching blue-collar workers to jobs over voice, in a dozen languages. "
        "That routing problem is the part I keep thinking about.\n\n"
        "I'm a final-year CS student. I built a scheduler that cut queue wait by half. "
        "Python and Postgres mostly.\n\n"
        "Would you take an intern this summer? Happy to send code."
    )
    draft = d(body)
    assert validate_style(draft) == []
    errs = validate_draft(draft, NEWS, "Vahan", "x", CLAIMS)
    assert any("does not name the company" in e for e in errs)


def test_validate_draft_threads_previous_body_into_the_followup_rule():
    draft = d(GOOD_GROUNDED_BODY, step=1)
    assert validate_draft(draft, NEWS, "Vahan", "x", CLAIMS) == []
    errs = validate_draft(draft, NEWS, "Vahan", "x", CLAIMS, previous_body="short intern ask")
    assert any("shorter" in e for e in errs)
