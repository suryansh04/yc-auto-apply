"""Spec §10's resume-claims rule, deferred from Task 9 and implemented in
Task 14 now that claims exist: a draft is rejected if it "asserts a skill,
metric or role absent from `resume_profile.claims`".

Kept deliberately conservative — it flags only *clear* fabrications: a
first-person sentence (the "about me" material) that names a technology /
tool / proper noun (a capitalised token mid-sentence) or a number that
appears in no claim's text or source span. Everything about the company
(its name, the cited source's own terms, the one-liner) is exempt, since
those are claims about *them*, not about the sender. Lower-case technology
mentions are not policed — the cost of a false positive (rejecting a true
draft) is a wasted retry, the cost of a miss is caught by the human
reviewer (D5), so the rule errs toward silence."""

import pytest

from app.domain.entities import Draft, ResumeClaim
from app.domain.validation import SourceRow, validate_claims, validate_draft

CLAIMS = [
    ResumeClaim(kind="project", text="built a scheduler in Python",
                source_span="Built a scheduler in Python, cut wait 50%"),
    ResumeClaim(kind="education", text="final-year CS student at IIT Delhi",
                source_span="B.Tech CSE, IIT Delhi, 2023-2027"),
]
NEWS = SourceRow(
    kind="news",
    text="OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI",
    publication="The Economic Times",
    intern_appropriate=None,
)


def d(body):
    return Draft(subject="Backend intern at Vahan", body=body,
                 research_source_type="news", research_source_id=1)


# --- positives: nothing to flag ---------------------------------------------

def test_a_sentence_naming_a_technology_that_is_in_a_claim_passes():
    body = "I built a scheduler in Python. Would you take an intern at Vahan?"
    assert validate_claims(d(body), CLAIMS) == []


def test_a_metric_present_in_a_claim_span_passes():
    body = "I built a scheduler in Python that cut wait 50%. Would you take an intern at Vahan?"
    assert validate_claims(d(body), CLAIMS) == []


def test_a_term_from_a_claim_span_but_not_its_text_still_counts():
    # "IIT Delhi" is only in the education claim's source_span, not its text.
    body = "I'm at IIT Delhi. Would you take an intern?"
    assert validate_claims(d(body), CLAIMS) == []


def test_the_reference_example_body_passes():
    body = (
        "You're matching blue-collar workers to jobs over voice, in a dozen languages, at Vahan. "
        "That routing problem is the part I keep thinking about.\n\n"
        "I'm a final-year CS student. I built a scheduler that cut queue wait by half. "
        "Python and Postgres mostly.\n\n"
        "Would you take an intern this summer? Happy to send code."
    )
    assert validate_claims(d(body), CLAIMS) == []


def test_non_first_person_sentences_are_not_policed():
    # A sentence about the company, not about the sender, may name anything.
    body = "Your Kubernetes migration at Vahan is the interesting part. Would you take an intern?"
    assert validate_claims(d(body), CLAIMS) == []


def test_company_terms_in_a_first_person_sentence_are_exempt():
    body = "I keep thinking about your voice AI at Vahan. Would you take an intern?"
    assert validate_claims(d(body), CLAIMS, allowed_text=["Vahan", NEWS.text]) == []


def test_claims_given_as_plain_dicts_work_too():
    claims = [{"kind": "project", "text": "built a scheduler in Python",
               "source_span": "Built a scheduler in Python"}]
    body = "I built a scheduler in Python. Would you take an intern?"
    assert validate_claims(d(body), claims) == []


def test_no_claims_at_all_flags_any_first_person_technology_mention():
    body = "I built a scheduler in Python. Would you take an intern?"
    assert validate_claims(d(body), []) != []


# --- negatives: clear fabrications ------------------------------------------

def test_a_sentence_fabricating_a_technology_not_in_any_claim_is_rejected():
    body = "I built a scheduler in Rust. Would you take an intern at Vahan?"
    errs = validate_claims(d(body), CLAIMS)
    assert any("Rust" in e and "claim" in e for e in errs)


def test_a_fabricated_metric_is_rejected():
    body = "I built a scheduler that cut wait by 90%. Would you take an intern?"
    errs = validate_claims(d(body), CLAIMS)
    assert any("90" in e for e in errs)


def test_a_fabricated_institution_is_rejected():
    body = "I'm a student at Stanford. Would you take an intern?"
    errs = validate_claims(d(body), CLAIMS)
    assert any("Stanford" in e for e in errs)


def test_my_and_me_count_as_first_person_too():
    body = "My scheduler runs on Kafka. Would you take an intern?"
    assert any("Kafka" in e for e in validate_claims(d(body), CLAIMS))


def test_a_first_person_contraction_is_not_itself_flagged():
    # "I've" / "I'm" start with a capital I but are pronouns, not tools.
    body = "I've built a scheduler in Python. Would you take an intern?"
    assert validate_claims(d(body), CLAIMS) == []


def test_only_the_first_offending_token_per_sentence_is_reported_once():
    body = "I built it in Rust with Kafka. Would you take an intern?"
    errs = validate_claims(d(body), CLAIMS)
    assert len(errs) == 1
    assert "Rust" in errs[0] and "Kafka" in errs[0]


# --- wiring: validate_draft enforces it --------------------------------------

def test_validate_draft_rejects_a_fabricated_technology():
    body = ("You're matching blue-collar workers to jobs over voice at Vahan. "
            "I built a scheduler in Rust. Would you take an intern?")
    errs = validate_draft(d(body), NEWS, "Vahan", "x", CLAIMS)
    assert any("Rust" in e for e in errs)


def test_validate_draft_exempts_the_company_name_and_source_terms():
    # "Vahan" (company) and "OpenAI" (from the cited source) appear in a
    # first-person sentence and are not in any claim — still fine.
    body = ("You're matching blue-collar workers to jobs over voice at Vahan. "
            "I'd like to work on what OpenAI backed at Vahan.\n\n"
            "I built a scheduler in Python. Would you take an intern?")
    errs = validate_draft(d(body), NEWS, "Vahan", "x", CLAIMS)
    assert errs == []


# --- fix round 1 ---------------------------------------------------------------

F4_CLAIMS = CLAIMS + [
    ResumeClaim(kind="skill", text="JavaScript", source_span="Skills: JavaScript"),
]
VAHAN_DESCRIPTION = ("Vahan screens and places delivery riders for Zomato and Swiggy over a "
                     "Hindi speech pipeline.")
F4_ALLOWED = ["Vahan", NEWS.text, VAHAN_DESCRIPTION, "Madhav Krishna"]


@pytest.mark.parametrize("sentence", [
    "I could start in May 2027 after my exams.",           # month name; 2027 is in the education span
    "I have 3 months free this summer.",                    # bare 1-digit number
    "I'd like to work with you, Madhav.",                   # founder's name (allowed_text)
    "I keep thinking about how you screen for Zomato and Swiggy.",  # long_description terms
    "I'd like to work on the Hindi speech pipeline.",       # long_description term
    "My code is on GitHub.",                                # stopword
    "I can share my LinkedIn on Monday.",                   # stopwords
])
def test_f4_table_rows_are_not_flagged(sentence):
    body = f"{sentence} Would you take an intern?"
    assert validate_claims(d(body), F4_CLAIMS, allowed_text=F4_ALLOWED) == []


def test_validate_draft_extra_allowed_text_exempts_description_and_founder_terms():
    body = ("You're matching blue-collar workers to jobs over voice at Vahan.\n\n"
            "I keep thinking about the Hindi speech pipeline, Madhav.\n\n"
            "I built a scheduler in Python. Would you take an intern?")
    assert any("Hindi" in e for e in validate_draft(d(body), NEWS, "Vahan", "x", CLAIMS))
    assert validate_draft(d(body), NEWS, "Vahan", "x", CLAIMS,
                          extra_allowed_text=[VAHAN_DESCRIPTION, "Madhav Krishna"]) == []


def test_f5a_substring_of_a_claim_token_does_not_license_a_different_technology():
    # A "JavaScript" claim must not make "Java" pass.
    body = "I built a scheduler in Java. Would you take an intern?"
    errs = validate_claims(d(body), F4_CLAIMS)
    assert any("Java" in e for e in errs)


def test_f5b_a_fabricated_metric_in_a_third_person_sentence_outside_the_hook_is_flagged():
    body = ("You're matching blue-collar workers to jobs over voice at Vahan.\n\n"
            "The scheduler cut wait by 90% across 10,000 jobs. I built it in Python.\n\n"
            "Would you take an intern?")
    errs = validate_claims(d(body), CLAIMS)
    assert len(errs) == 1
    assert "90%" in errs[0] and "10,000" in errs[0]


def test_numbers_in_the_hook_paragraph_are_left_to_the_grounding_check():
    body = ("You route 40,000 calls a day at Vahan.\n\n"
            "I built a scheduler in Python.\n\n"
            "Would you take an intern?")
    assert validate_claims(d(body), CLAIMS) == []


def test_numbers_in_a_single_paragraph_third_person_sentence_are_not_policed():
    # One paragraph means the whole body is the hook; only first-person
    # sentences are policed for numbers there.
    body = "You route 40,000 calls a day at Vahan. Would you take an intern?"
    assert validate_claims(d(body), CLAIMS) == []


def test_f13_comma_separated_number_matches_its_claim_without_commas():
    claims = [ResumeClaim(kind="metric", text="handled 10000 jobs", source_span="handled 10000 jobs")]
    body = "I handled 10,000 jobs. Would you take an intern?"
    assert validate_claims(d(body), claims) == []
    claims = [ResumeClaim(kind="metric", text="handled 10,000 jobs", source_span="handled 10,000 jobs")]
    body = "I handled 10000 jobs. Would you take an intern?"
    assert validate_claims(d(body), claims) == []


def test_a_two_digit_fabricated_number_is_still_flagged():
    body = "I have 12 side projects. Would you take an intern?"
    assert any("12" in e for e in validate_claims(d(body), CLAIMS))


def test_two_letter_abbreviations_are_exempt():
    # Pins _MIN_PROPER_TOKEN_LEN = 3 (reviewer's M12).
    body = "I'm a CS student who likes ML and AI. Would you take an intern?"
    assert validate_claims(d(body), CLAIMS) == []


def test_a_three_letter_all_caps_technology_is_policed():
    body = "I run everything on AWS. Would you take an intern?"
    assert any("AWS" in e for e in validate_claims(d(body), CLAIMS))
