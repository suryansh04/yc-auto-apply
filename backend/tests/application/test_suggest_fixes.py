"""`run_reviser` decides what is worth putting in a hover card.

The model is not trusted here. Every test below is a shape a real model
answers with sooner or later - an index for a span nobody sent, the
flagged text handed back unchanged, the whole paragraph in place of one
sentence - and the rule is the same for all of them: a suggestion that
cannot be shown honestly is dropped, and the checks stay on screen
regardless.
"""

import pytest

from app.application.suggest_fixes import Suggestion, run_reviser

BODY = "I read about the launch. The distinction struck me because most founders would not."

HIGHLIGHTS = [
    {"message": "sentence exceeds 45 words", "quote": "The distinction struck me because most founders would not."},
    {"message": "body exceeds 380 words", "quote": None},
]


class Reviser:
    """Answers with whatever the test wants, and records what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.context = None

    def revise(self, context):
        self.context = context
        return self.answer() if callable(self.answer) else self.answer


def _run(answer, highlights=HIGHLIGHTS):
    reviser = Reviser(answer)
    return reviser, run_reviser(reviser, BODY, highlights, "Loop Health")


def test_a_rewrite_comes_back_against_the_note_it_answers():
    _, out = _run([{"index": 0, "replacement": "Most founders stop at the pitch.", "why": "shorter"}])
    assert out == [Suggestion(index=0, replacement="Most founders stop at the pitch.", why="shorter")]


def test_only_spans_with_words_to_rewrite_are_sent():
    # The second note is about the letter's length. There is no phrase to
    # rewrite, so the model is never asked about it - but the index it
    # would have had is still 1, so a caller can pair by index alone.
    reviser, _ = _run([])
    assert [s["index"] for s in reviser.context["spans"]] == [0]


def test_the_whole_letter_goes_along_for_context():
    reviser, _ = _run([])
    assert reviser.context["body"] == BODY
    assert reviser.context["company_name"] == "Loop Health"


def test_an_index_for_a_span_nobody_sent_is_dropped():
    _, out = _run([{"index": 7, "replacement": "anything", "why": ""}])
    assert out == []


def test_the_flagged_text_handed_back_is_not_a_suggestion():
    # "change it to what it already says" is noise in a hover card.
    _, out = _run([{"index": 0, "replacement": "  the DISTINCTION struck me because most founders would not.  ",
                    "why": "no change"}])
    assert out == []


def test_a_replacement_that_is_really_the_whole_paragraph_is_dropped():
    _, out = _run([{"index": 0, "replacement": "x" * 400, "why": "rewrote everything"}])
    assert out == []


def test_deleting_the_span_is_a_real_suggestion():
    _, out = _run([{"index": 0, "replacement": "", "why": "this sentence does no work"}])
    assert [s.replacement for s in out] == [""]


def test_a_second_rewrite_for_the_same_span_is_ignored():
    _, out = _run([
        {"index": 0, "replacement": "First.", "why": "a"},
        {"index": 0, "replacement": "Second.", "why": "b"},
    ])
    assert [s.replacement for s in out] == ["First."]


def test_a_reviser_that_raises_costs_nothing():
    # Advice on top of advice: the checks are already on screen and must
    # stay there. Nothing propagates.
    def boom():
        raise RuntimeError("sk-live-0000 should never reach a log line")

    _, out = _run(boom)
    assert out == []


def test_a_failure_is_logged_by_type_only(caplog):
    def boom():
        raise RuntimeError("sk-live-0000")

    with caplog.at_level("WARNING"):
        _run(boom)
    assert "RuntimeError" in caplog.text
    assert "sk-live-0000" not in caplog.text


def test_nothing_highlighted_means_no_call_at_all():
    reviser = Reviser([{"index": 0, "replacement": "no", "why": ""}])
    assert run_reviser(reviser, BODY, [{"message": "body exceeds 380 words", "quote": None}]) == []
    assert reviser.context is None


@pytest.mark.parametrize("junk", [None, "a string", [None], [{"replacement": "no index"}],
                                  [{"index": "zero", "replacement": "x"}]])
def test_junk_is_survived(junk):
    _, out = _run(junk)
    assert out == []


def test_cutting_a_whole_paragraph_is_not_a_rewrite_of_it():
    # Live finding (2026-09-23, Loop Health): asked about a coarse note -
    # one that names no phrase, so it points at the whole company
    # paragraph - Sarvam answered with an empty replacement. Pressing
    # "Cut it" would have deleted the only part of the letter that is
    # about the founder's company.
    paragraph = ("In the funding announcement you did not chase a generic employee wellness "
                 "brochure. You chose to build a dedicated plan for India's workforce instead. "
                 "The distinction struck me because most founders would have stopped there.")
    highlights = [{"message": "hook shares no distinctive term with the cited source",
                   "quote": paragraph}]
    _, out = _run([{"index": 0, "replacement": "", "why": "removes unsupported claim"}], highlights)
    assert out == []
