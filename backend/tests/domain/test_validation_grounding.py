# backend/tests/domain/test_validation_grounding.py
from app.domain.entities import Draft, ResumeClaim
from app.domain.validation import validate_grounding, SourceRow

NEWS = SourceRow(
    kind="news",
    text="OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI",
    publication="The Economic Times",
    intern_appropriate=None,
)
CLAIMS = [ResumeClaim(kind="project", text="built a scheduler in Python", source_span="...")]
GOOD = ("You're matching blue-collar workers to jobs over voice at Vahan. "
        "That routing problem is what I keep thinking about. "
        "I built a scheduler in Python. Would you take an intern?")

def d(body=GOOD, stype="news", sid=1):
    return Draft(subject="Backend intern at Vahan", body=body,
                 research_source_type=stype, research_source_id=sid)

def test_grounded_draft_passes():
    assert validate_grounding(d(), NEWS, "Vahan", "Recruitment over voice", CLAIMS) == []

def test_unresolved_source_is_rejected():
    errs = validate_grounding(d(sid=None), None, "Vahan", "x", CLAIMS)
    assert any("does not resolve" in e for e in errs)

def test_rejects_source_as_subject_phrasing():
    body = "Saw the article on your voice AI for blue-collar hiring. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("refers to the source" in e for e in errs)

def test_rejects_publication_name_in_body():
    body = ("The Economic Times wrote about your blue-collar hiring voice AI at Vahan. "
            "Would you take an intern?")
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("publication" in e for e in errs)

def test_loophole_only_overlap_is_publication_name_fails_grounding():
    # D29: naming the outlet must not by itself satisfy grounding.
    body = "Economic Times. Vahan is great. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("publication" in e or "shares no distinctive term" in e for e in errs)

def test_rejects_when_no_distinctive_term_shared():
    body = "Your product looks good at Vahan. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("shares no distinctive term" in e for e in errs)

def test_rejects_missing_company_name():
    body = ("You're matching blue-collar workers to jobs over voice. "
            "Would you take an intern?")
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("does not name the company" in e for e in errs)

def test_rejects_non_intern_appropriate_job_posting():
    posting = SourceRow(kind="job_posting", text="Senior Engineer",
                        publication=None, intern_appropriate=False)
    body = "Your Senior Engineer opening at Vahan caught my eye. Would you take an intern?"
    errs = validate_grounding(d(body=body, stype="job_posting"), posting, "Vahan", "x", CLAIMS)
    assert any("not intern-appropriate" in e for e in errs)

def test_rejects_hook_that_parrots_the_one_liner():
    one_liner = "Recruitment platform for blue-collar workers using voice"
    body = one_liner + " at Vahan. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", one_liner, CLAIMS)
    assert any("one_liner" in e for e in errs)


# --- Fix round 1 coverage (I1, I5) ---

def test_publication_terms_are_subtracted_even_when_source_text_contains_them():
    # D29, real regression: a headline like "Economic Times: OpenAI backs
    # Vahan" puts the outlet's own words inside source.text. If those words
    # (validation.py's pub_terms) aren't subtracted from the source's
    # substantive terms before checking for a shared distinctive term, a
    # draft that only echoes the outlet name passes grounding by accident.
    # Deleting `- pub_terms` at validation.py's `substantive = ...` line
    # leaves this failing while all other grounding tests stay green.
    source = SourceRow(kind="news", text="Economic Times reports a funding round",
                        publication="The Economic Times", intern_appropriate=None)
    body = "Your Economic Times moment at Vahan. Would you take an intern?"
    errs = validate_grounding(d(body=body), source, "Vahan", "x", CLAIMS)
    assert any("publication" in e or "shares no distinctive term" in e for e in errs)


def test_hook_grounding_ignores_a_distinctive_term_planted_in_a_later_paragraph():
    # The source-sharing check must score only the hook (first paragraph),
    # not the whole body — otherwise a generic opener passes grounding
    # because a later paragraph happens to reuse a source word.
    body = ("Your product looks good at Vahan.\n\n"
            "I built a scheduler in Python. Also, blue-collar hiring over voice is neat. "
            "Would you take an intern?")
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("shares no distinctive term" in e for e in errs)


def test_hook_one_liner_overlap_ignores_a_later_paragraph():
    # Mirror image of the above: the one_liner-overlap check must also
    # score only the hook, so parroting the one_liner in paragraph two (not
    # the hook) doesn't over-fire the overlap rule.
    one_liner = "Recruitment platform for blue-collar workers using voice"
    body = ("You're automating blue-collar hiring at Vahan. That's what caught my eye.\n\n"
            + one_liner + " is roughly how I'd describe it. Would you take an intern?")
    errs = validate_grounding(d(body=body), NEWS, "Vahan", one_liner, CLAIMS)
    assert not any("one_liner" in e for e in errs)
