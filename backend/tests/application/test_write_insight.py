"""`run_insight_writer` - what comes back from the model is not trusted.

The rule this module lives by is `suggest_fixes`'s: advice can never fail
loudly. Nothing here raises, and everything unusable is dropped quietly
rather than offered to the operator.
"""
import logging

from app.application.write_insight import (
    MAX_WORDS,
    MIN_WORDS,
    run_insight_writer,
    unusable_reason,
)

NAME = "Loop Health"
ONE_LINER = "Modern & comprehensive health insurance plan for employees in India"
DESCRIPTION = (
    "Loop Health combines primary care and insurance for employers. Employees get "
    "unlimited primary care visits at our asset-light clinics."
)
# Deliberately NOT the operator's Loop Health line: that wording is now
# what a template answer looks like (see `_FORMULA_TELLS`), and a test
# fixture that trips the check it is testing teaches nothing.
GOOD = (
    "I read about how Loop Health gives employees unlimited primary care visits at its "
    "own clinics, alongside the usual hospital cover. Health cover here usually pays out "
    "only when someone is admitted, so the years where nothing goes badly wrong feel like "
    "paying for nothing. I'm curious how you decide which clinic visits the plan should "
    "cover as more employers join. I'd love to learn how your team thinks about that."
)
# Passes the checklist, so the only thing wrong with it is the template.
TEMPLATE = (
    "I like that you put doctors and insurers on the same side. Most health insurance in "
    "India only pays out at a hospital, so people barely use what they buy. How do you get "
    "employees to actually use the clinics? You made it something people use day to day, "
    "which is the kind of thing I want to build."
)


class Writer:
    def __init__(self, answer=None, raises=None):
        self.answer = answer if answer is not None else {"paragraph": GOOD, "why": "the choice"}
        self.raises = raises
        self.context = None

    def write(self, context):
        self.context = context
        if self.raises:
            raise self.raises
        return self.answer


def _run(writer):
    return run_insight_writer(writer, NAME, ONE_LINER, DESCRIPTION)


def test_a_usable_paragraph_comes_back_with_its_reason():
    assert _run(Writer()) == {
        "options": [{"style": "excited", "paragraph": GOOD}], "why": "the choice",
    }


def test_the_model_is_handed_the_description_and_nothing_about_the_letter():
    writer = Writer()
    _run(writer)
    assert writer.context == {
        "company_name": NAME, "one_liner": ONE_LINER, "description": DESCRIPTION,
        "built": "", "research": None, "avoid": [],
    }


def test_a_company_with_no_description_is_never_asked():
    """Nothing to write from, so there is nothing to pay a model for."""
    writer = Writer()
    assert run_insight_writer(writer, NAME, ONE_LINER, "   ") is None
    assert writer.context is None


def test_a_writer_that_raises_yields_nothing_and_never_raises():
    assert _run(Writer(raises=RuntimeError("boom"))) is None


def test_a_failure_is_logged_by_type_name_only(caplog):
    """A provider's message can carry a key. Only the exception's type
    name may be written down."""
    with caplog.at_level(logging.WARNING):
        _run(Writer(raises=RuntimeError("401: key sk_live_secret is bad")))
    assert "sk_live_secret" not in caplog.text
    assert "RuntimeError" in caplog.text


# --- what counts as usable -------------------------------------------------


def test_an_empty_paragraph_is_a_legitimate_answer_and_is_dropped():
    # The prompt asks for one when the description is too thin to say
    # anything true. A generic line already exists for that case.
    assert unusable_reason("") is not None
    assert _run(Writer({"paragraph": "", "why": ""})) is None


def test_a_number_from_their_yc_page_is_refused():
    # 2026-09-25: numbers from the facts are fine ("49 plant genomes") -
    # but never one they did not write, and never an investor number.
    assert "not in what they wrote" in unusable_reason(
        GOOD.replace("the years", "the 22 years"), "a description with no digits")
    assert "investor" in unusable_reason(GOOD.replace("usually", "in 70 percent of cases"))


def test_numbers_they_wrote_are_allowed():
    text = GOOD.replace("the years", "the 22 years")
    assert unusable_reason(text, "We have run 22 years of clinics.") is None


def test_a_company_name_with_a_digit_is_not_a_number():
    # "Y42", "8base": a digit inside a name is not a fact quoted off a
    # fundraising page, and dropping those paragraphs would make the
    # feature useless for those companies.
    assert unusable_reason(GOOD.replace("Loop", "Y42")) is None


def test_the_tells_the_letter_bans_everywhere_else_are_banned_here():
    assert unusable_reason("I am reaching out because " + GOOD)
    assert unusable_reason(GOOD.replace("Health cover", "I signed up and tried it. Health cover"))


def test_his_documents_opening_is_allowed():
    # "I came across Aadi on Hugging Face" - his own example.
    assert unusable_reason(GOOD.replace("I read about how", "I came across how")) is None


def test_markdown_and_dashes_are_refused():
    assert unusable_reason("**Loop** " + GOOD) == "not plain text"
    assert "dash" in unusable_reason(GOOD.replace("own clinics,", "own clinics —"))


def test_it_is_at_most_the_tie_in_and_the_company_paragraph():
    assert unusable_reason(GOOD + "\n\n" + GOOD + "\n\n" + GOOD)


def test_length_is_his_50_to_110_words():
    assert "50 to 110" in unusable_reason("I like what you built. How does it work?")
    assert "110" in unusable_reason(GOOD + " " + GOOD.replace("?", "."))
    assert 50 <= len(GOOD.split()) <= 110


def test_his_checklist_is_applied():
    assert "question" in unusable_reason(GOOD.replace("I'm curious how you decide", "You decide"))
    assert "instead of" in unusable_reason(GOOD.replace("alongside", "instead of"))
    assert "caught my eye" in unusable_reason(GOOD.replace("I read about", "What caught my eye is"))


def test_the_case_study_symmetry_is_refused():
    """Live finding (2026-09-23): "no this looks properly ai generated" -
    a formal setup, "Usually ...", then "Instead you ...". The symmetry
    is the tell, not the vocabulary."""
    assert unusable_reason(
        "You could have just listed operators without touching their schedules, and you did "
        "not. Usually an aggregator dumps every route into one messy list and leaves the "
        "operator to figure out timing alone. How do you set schedules for them without "
        "losing the operators you already have on board? Instead you built a dedicated "
        "platform that sets schedules for them."
    )


# --- the same paragraph for every company ---------------------------------
#
# Live finding (2026-09-23): the operator read the paragraphs for
# Orangewood Labs and zingbus and said "it is showing the same for
# orangewood and zingbus". Not the same sentence - the same template,
# lifted off the worked example in the prompt.


def test_the_examples_template_is_refused():
    assert "template" in unusable_reason(TEMPLATE)


class TwoAnswers:
    """Answers with each paragraph in turn, recording every context."""

    def __init__(self, *paragraphs):
        self.paragraphs = list(paragraphs)
        self.contexts = []

    def write(self, context):
        self.contexts.append(context)
        return {"paragraph": self.paragraphs[len(self.contexts) - 1], "why": ""}


def test_a_template_answer_is_asked_once_more_rather_than_dropped():
    writer = TwoAnswers(TEMPLATE, GOOD)
    assert _run(writer)["options"][0]["paragraph"] == GOOD
    assert len(writer.contexts) == 2
    assert TEMPLATE in writer.contexts[1]["avoid"]


def test_it_gives_up_after_the_second_template():
    writer = TwoAnswers(TEMPLATE, TEMPLATE)
    assert _run(writer) is None
    assert len(writer.contexts) == 2


def test_other_rejections_are_not_retried():
    writer = TwoAnswers(GOOD.replace("the years", "the 40 years"), GOOD)
    assert _run(writer) is None
    assert len(writer.contexts) == 1


def test_what_the_letter_already_says_is_never_offered_back():
    writer = TwoAnswers(GOOD, GOOD.replace("more employers join", "new employers sign up"))
    out = run_insight_writer(writer, NAME, ONE_LINER, DESCRIPTION, avoid=[GOOD])
    assert out["options"][0]["paragraph"] != GOOD
    assert GOOD in writer.contexts[0]["avoid"]


# --- three options -----------------------------------------------------------

ANGLE = (
    "I came across the way Loop Health runs its own asset-light clinics for employers and "
    "keeps hospital cover alongside them. How do you decide where a new clinic goes when a "
    "large employer signs up in a city you are not in yet? Getting that wrong seems "
    "expensive for everyone, and planning around it is the kind of work I'd love to do."
)
SHORT = (
    "I read that Loop Health gives employees unlimited primary care visits at its own "
    "clinics, on top of the usual hospital cover. I'm curious how you keep unlimited visits "
    "affordable for an employer as the team keeps growing each quarter. I'd love to learn how your whole team "
    "approaches that."
)


class Options:
    def __init__(self, *options):
        self.options = list(options)
        self.context = None

    def write(self, context):
        self.context = context
        return {"options": self.options, "why": "the clinic choice"}


def test_all_three_styles_come_back_when_all_three_are_usable():
    out = _run(Options({"style": "template", "paragraph": GOOD},
                       {"style": "angle", "paragraph": ANGLE},
                       {"style": "short", "paragraph": SHORT}))
    assert [o["style"] for o in out["options"]] == ["template", "angle", "short"]


def test_a_bad_option_is_dropped_and_the_rest_are_still_offered():
    out = _run(Options({"style": "template", "paragraph": TEMPLATE},
                       {"style": "short", "paragraph": SHORT}))
    assert [o["paragraph"] for o in out["options"]] == [SHORT]


def test_two_identical_options_are_one_option():
    out = _run(Options({"style": "angle", "paragraph": SHORT},
                       {"style": "short", "paragraph": SHORT}))
    assert len(out["options"]) == 1


def test_what_he_has_built_is_passed_through_as_the_only_thing_about_him():
    writer = Options({"style": "short", "paragraph": SHORT})
    run_insight_writer(writer, NAME, ONE_LINER, DESCRIPTION, built="1. WaveWatch: ...")
    assert writer.context["built"] == "1. WaveWatch: ..."


def test_the_verified_research_is_handed_to_the_writer():
    writer = Options({"style": "short", "paragraph": SHORT})
    research = {"facts": [{"kind": "specific_thing", "text": "Clinics.", "old": False}]}
    run_insight_writer(writer, NAME, ONE_LINER, DESCRIPTION, research=research)
    assert writer.context["research"] == research
