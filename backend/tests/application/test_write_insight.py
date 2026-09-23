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
    "I really like how you're not just selling a policy and leaving the clinic out of it. "
    "Health cover here usually pays out only when someone is admitted, so the years where "
    "nothing goes badly wrong are years of paying for nothing. Putting the everyday visits "
    "inside the cover changes what a person actually gets for it."
)
TEMPLATE = (
    "I like that you put doctors and insurers on the same side. Most health insurance in "
    "India only pays out at a hospital, so people barely use what they buy. You made it "
    "something people use day to day, which is the kind of thing I want to build."
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
    assert _run(Writer()) == {"paragraph": GOOD, "why": "the choice"}


def test_the_model_is_handed_the_description_and_nothing_about_the_letter():
    writer = Writer()
    _run(writer)
    assert writer.context == {
        "company_name": NAME, "one_liner": ONE_LINER, "description": DESCRIPTION, "avoid": [],
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
    assert "number" in unusable_reason(GOOD.replace("the years", "the 22 years"))
    assert "number" in unusable_reason(GOOD.replace("usually", "70 percent of the time"))


def test_a_company_name_with_a_digit_is_not_a_number():
    # "Y42", "8base": a digit inside a name is not a fact quoted off a
    # fundraising page, and dropping those paragraphs would make the
    # feature useless for those companies.
    assert unusable_reason(GOOD.replace("Loop", "Y42")) is None


def test_the_tells_the_letter_bans_everywhere_else_are_banned_here():
    assert unusable_reason("I am reaching out because " + GOOD)
    assert unusable_reason("I came across Loop Health and " + GOOD)
    assert unusable_reason(GOOD.replace("Putting", "I signed up and tried it. Putting"))


def test_markdown_is_refused_because_the_letter_is_plain_text():
    assert unusable_reason("**Loop** " + GOOD) == "not plain text"
    assert unusable_reason(GOOD.replace("the clinic", "the clinic —")) == "not plain text"


def test_it_has_to_be_one_paragraph():
    assert unusable_reason(GOOD + "\n\n" + GOOD) == "more than one paragraph"


def test_length_is_bounded_at_both_ends():
    assert "too short" in unusable_reason("I like what you built.")
    assert "too long" in unusable_reason(" ".join(["word"] * (MAX_WORDS + 1)))
    assert MIN_WORDS < len(GOOD.split()) < MAX_WORDS


def test_the_voice_the_operator_asked_for_passes():
    """"I really like how you are not dumping every route into a messy
    list and instead actually built a dedicated platform" - his words for
    what this should sound like. If the rules here would drop it, the
    rules are wrong."""
    assert unusable_reason(GOOD) is None
    assert unusable_reason(
        "I really like how you're not dumping every route into one messy list and instead "
        "actually built a dedicated platform for intercity buses. Most aggregators are happy "
        "being a search box, so nobody owns whether the bus runs on time. Running the "
        "schedule yourself means the person waiting at the pickup point has someone who cares."
    ) is None


def test_the_case_study_symmetry_is_refused():
    """Live finding (2026-09-23): "no this looks properly ai generated" -
    a formal setup, "Usually ...", then "Instead you ...". The symmetry
    is the tell, not the vocabulary."""
    assert "template" in unusable_reason(
        "You could have just listed operators without touching their schedules, and you did "
        "not. Usually an aggregator dumps every route into one messy list and leaves the "
        "operator to figure out timing alone. Instead you built a dedicated platform that "
        "sets schedules for them."
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
    # Silence is a worse answer than a second attempt, and this is a
    # failure the model can correct when shown what it just wrote.
    writer = TwoAnswers(TEMPLATE, GOOD)
    assert _run(writer)["paragraph"] == GOOD
    assert len(writer.contexts) == 2
    assert TEMPLATE in writer.contexts[1]["avoid"]


def test_it_gives_up_after_the_second_template():
    writer = TwoAnswers(TEMPLATE, TEMPLATE)
    assert _run(writer) is None
    assert len(writer.contexts) == 2


def test_other_rejections_are_not_retried():
    # A model that quoted a number or invented a detail is not more
    # trustworthy on the second ask; only the template is worth another.
    writer = TwoAnswers(GOOD.replace("the years", "the 40 years"), GOOD)
    assert _run(writer) is None
    assert len(writer.contexts) == 1


def test_what_the_letter_already_says_is_never_offered_back():
    writer = TwoAnswers(GOOD, GOOD + " And one more thought about it.")
    out = run_insight_writer(writer, NAME, ONE_LINER, DESCRIPTION, avoid=[GOOD])
    assert out["paragraph"] != GOOD
    assert GOOD in writer.contexts[0]["avoid"]
