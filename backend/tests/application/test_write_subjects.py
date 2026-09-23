"""`run_subject_writer` - subject lines written per company (operator
request 2026-09-23: "a agent specifically for writing random catchy
punchy subject instead of generic one").

What comes back is not trusted: the rules here are what keep a "catchy"
subject from being a dishonest or spammy one. And advice never fails
loudly - nothing here raises.
"""
import logging

from app.application.draft_outreach import DraftOutreach
from app.application.write_subjects import OFFERED, run_subject_writer, subject_problem
from app.domain import letter


class Writer:
    def __init__(self, answer=None, raises=None):
        self.answer = answer if answer is not None else [
            "the agents GroMo actually trusts",
            "an intern who wants to build for GroMo",
            "why the agent is the product",
        ]
        self.raises = raises
        self.context = None

    def write(self, context):
        self.context = context
        if self.raises:
            raise self.raises
        return self.answer


def _run(writer, **kw):
    return run_subject_writer(writer, "GroMo", "Sell financial products", "Agents sell.", **kw)


def test_three_usable_subjects_come_back_best_first():
    assert _run(Writer()) == [
        "the agents GroMo actually trusts",
        "an intern who wants to build for GroMo",
        "why the agent is the product",
    ]


def test_the_writer_is_given_the_company_and_its_paragraph():
    writer = Writer()
    run_subject_writer(writer, "GroMo", "one liner", "their description", paragraph="the para")
    assert writer.context["company_name"] == "GroMo"
    assert writer.context["description"] == "their description"
    assert writer.context["paragraph"] == "the para"


def test_a_failure_yields_nothing_and_is_logged_by_type_name_only(caplog):
    with caplog.at_level(logging.WARNING):
        assert _run(Writer(raises=RuntimeError("401: key sk_live_secret"))) == []
    assert "sk_live_secret" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_never_more_than_offered():
    many = [f"GroMo angle number {w}" for w in ("one", "two", "three", "four", "five")]
    assert len(_run(Writer(many))) == OFFERED


def test_what_was_already_offered_is_not_offered_again():
    out = _run(Writer(), avoid=["the agents GroMo actually trusts"])
    assert "the agents GroMo actually trusts" not in out


# --- the rules ----------------------------------------------------------


def test_a_fake_reply_or_forward_is_refused():
    # He has never written to this founder. Pretending otherwise is a lie.
    assert subject_problem("Re: your agent platform", "GroMo")
    assert subject_problem("Fwd: GroMo internship idea", "GroMo")


def test_the_subjects_every_founder_deletes_are_refused():
    for s in ("Internship inquiry for GroMo", "Quick question about GroMo",
              "An opportunity at GroMo", "Reaching out about GroMo"):
        assert subject_problem(s, "GroMo"), s


def test_shouting_numbers_punctuation_and_emoji_are_refused():
    assert subject_problem("GroMo agents are URGENT", "GroMo")
    assert subject_problem("GroMo grew 200 percent", "GroMo")
    assert subject_problem("why GroMo bets on agents!", "GroMo")
    assert subject_problem("is GroMo hiring interns?", "GroMo")
    assert subject_problem("GroMo agents \U0001F680 rocket", "GroMo")


def test_a_digit_or_capitals_in_the_company_s_own_name_are_fine():
    assert subject_problem("what Y42 gets right about models", "Y42") is None
    assert subject_problem("the HDFC side of lending", "HDFC") is None


def test_length_matches_the_letter_s_own_subject_check():
    assert subject_problem("GroMo", "GroMo")
    assert subject_problem(" ".join(["word"] * 11), "GroMo")


def test_the_operator_s_fixed_subject_passes_its_own_rules():
    # The fallback has to be a subject these rules would accept.
    assert subject_problem(letter.subject("GroMo"), "GroMo") is None


# --- at draft time ------------------------------------------------------


class _Company:
    name = "GroMo"
    one_liner = "Sell financial products"
    long_description = "Agents sell insurance and loans to people they know."


def _assembled_draft():
    from app.application.draft_outreach import _to_draft

    return _to_draft({"subject": "x", "company_paragraphs": ["They sell."],
                      "could_be_a_user": False}, 0, "GroMo")


def test_a_new_first_email_gets_the_first_usable_written_subject(repos):
    writer = Writer(["Re: GroMo", "the agents GroMo actually trusts"])
    draft = DraftOutreach(None, repos, subject_writer=writer)._with_written_subject(
        _assembled_draft(), _Company())
    # The fake reply is refused; the first usable one is taken.
    assert draft.subject == "the agents GroMo actually trusts"
    # ...and the writer was shown the company paragraph it is titling.
    assert writer.context["paragraph"] == "They sell."


def test_nothing_usable_keeps_the_fixed_subject(repos):
    writer = Writer(["Internship inquiry for GroMo"])
    draft = DraftOutreach(None, repos, subject_writer=writer)._with_written_subject(
        _assembled_draft(), _Company())
    assert draft.subject == letter.subject("GroMo")


def test_a_writer_that_fails_keeps_the_fixed_subject(repos):
    writer = Writer(raises=RuntimeError("provider down"))
    draft = DraftOutreach(None, repos, subject_writer=writer)._with_written_subject(
        _assembled_draft(), _Company())
    assert draft.subject == letter.subject("GroMo")


def test_without_a_writer_the_fixed_subject_stays():
    from app.application.draft_outreach import _to_draft

    draft = _to_draft({"subject": "x", "company_paragraphs": ["They sell."],
                       "could_be_a_user": False}, 0, "GroMo")
    assert draft.subject == letter.subject("GroMo")


def test_the_company_name_is_always_spelled_exactly_as_given():
    # Live finding: "an intern who wants to build tools for groMo's agents".
    out = run_subject_writer(Writer(["tools for groMo's agents"]), "GroMo")
    assert out == ["tools for GroMo's agents"]
