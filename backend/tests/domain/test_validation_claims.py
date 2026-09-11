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
