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
GOOD = (
    "I like how Loop puts doctors and insurers on the same side. Most health insurance in "
    "India only kicks in when you land in a hospital, so people pay for it every year and "
    "barely use it. You turned it into something people actually use day to day, and that "
    "is the kind of thing I want to build."
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
        "company_name": NAME, "one_liner": ONE_LINER, "description": DESCRIPTION,
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
    assert "number" in unusable_reason(GOOD.replace("every year", "22 companies"))
    assert "number" in unusable_reason(GOOD.replace("day to day", "70% of the time"))


def test_a_company_name_with_a_digit_is_not_a_number():
    # "Y42", "8base": a digit inside a name is not a fact quoted off a
    # fundraising page, and dropping those paragraphs would make the
    # feature useless for those companies.
    assert unusable_reason(GOOD.replace("Loop", "Y42")) is None


def test_the_tells_the_letter_bans_everywhere_else_are_banned_here():
    assert unusable_reason(GOOD.replace("I like how", "I am reaching out because I like how"))
    assert unusable_reason("I came across Loop Health and " + GOOD)
    assert unusable_reason(GOOD.replace("You turned it", "I signed up and tried it, and you turned it"))


def test_markdown_is_refused_because_the_letter_is_plain_text():
    assert unusable_reason("**Loop** " + GOOD) == "not plain text"
    assert unusable_reason(GOOD.replace("same side.", "same side —")) == "not plain text"


def test_it_has_to_be_one_paragraph():
    assert unusable_reason(GOOD + "\n\n" + GOOD) == "more than one paragraph"


def test_length_is_bounded_at_both_ends():
    assert "too short" in unusable_reason("I like what you built.")
    assert "too long" in unusable_reason(" ".join(["word"] * (MAX_WORDS + 1)))
    assert MIN_WORDS < len(GOOD.split()) < MAX_WORDS


def test_the_operators_own_example_passes():
    """The paragraph he asked for is the standard. If the rules here
    would drop it, the rules are wrong."""
    assert unusable_reason(GOOD) is None
