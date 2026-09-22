"""Turning a check's note into the exact words it is about.

Operator request (2026-09-22): mark the offending text in the letter and
offer the note beside it, the way a writing assistant does.
"""
from app.domain.highlight import highlights_for, span_for

BODY = (
    "This'll take 2.5 mins and I promise it is not another cold mail.\n\n"
    "Now Here's the Important Part:\n\n"
    "In the funding announcement you did not chase a generic brochure. "
    "You chose to build a dedicated plan instead.\n\n"
    "I can see that you have great users and growth, these users come with "
    "issues they face. I'd like to be one of the people fixing it as an intern."
)


def _quotes(notes, body=BODY):
    return [h["quote"] for h in highlights_for(notes, body)]


def test_a_quoted_note_resolves_to_a_real_substring():
    note = "tells a story about the sender that names nothing from the resume: 'You chose to build a dedicated plan instead.'"
    span = span_for(note, BODY)
    assert span is not None and span in BODY


def test_a_truncated_quote_grows_back_to_its_sentence():
    """The validator truncates to 40 characters, which would highlight
    half a sentence and stop mid-word."""
    note = "asserts something found in no resume claim: X in 'I can see that you have great users and'"
    span = span_for(note, BODY)
    assert span.startswith("I can see that you have great users")
    assert span.endswith("issues they face.")


def test_a_quote_that_is_not_in_the_body_highlights_nothing():
    """Marking the wrong words would be worse than marking none."""
    note = "banned phrase: 'genuinely'"
    assert span_for(note, BODY) is None


def test_a_phrase_is_found_whatever_its_case():
    """The phrase bans match a lower-cased body, so the quote comes back
    lower-cased even when the letter capitalises it."""
    body = "Genuinely, I have been thinking about this."
    assert span_for("banned phrase: 'genuinely'", body) == "Genuinely"


def test_a_hook_note_points_at_the_company_paragraph():
    """There is no phrase to point at, but there is a region - and it is
    the one the operator would want to look at."""
    span = span_for("hook shares no distinctive term with the cited source", BODY)
    assert span is not None
    assert "In the funding announcement" in span
    assert "This'll take 2.5 mins" not in span


def test_a_note_about_the_whole_letter_highlights_nothing():
    for note in ("body exceeds 380 words", "more than 11 paragraphs",
                 "subject is not between 3 and 10 words"):
        assert span_for(note, BODY) is None


def test_a_semicolon_is_pointed_at_exactly():
    assert span_for("contains a semicolon", "One thing; another thing.") == ";"


def test_a_bracketed_citation_is_pointed_at_exactly():
    body = "You chose the harder path [17]."
    assert span_for("contains a bracketed citation like '[3]'", body) == "[17]"


def test_every_note_comes_back_even_without_a_span():
    notes = ["body exceeds 380 words", "contains a semicolon"]
    out = highlights_for(notes, BODY)
    assert [h["message"] for h in out] == notes
    assert all(h["quote"] is None for h in out)


def test_overlapping_spans_keep_the_longest():
    """A highlight inside a highlight renders as neither."""
    body = "I built a scheduler in Python and it worked."
    notes = [
        "banned phrase: 'Python'",
        "tells a story about the sender: 'I built a scheduler in Python and it worked.'",
    ]
    quotes = [h["quote"] for h in highlights_for(notes, body)]
    assert quotes[1] == body            # the sentence wins
    assert quotes[0] is None            # the word inside it is dropped


def test_two_separate_spans_both_survive():
    body = "One thing; another. A totally different sentence [4]."
    notes = ["contains a semicolon", "contains a bracketed citation like '[3]'"]
    assert [h["quote"] for h in highlights_for(notes, body)] == [";", "[4]"]


def test_an_empty_note_list_is_an_empty_result():
    assert highlights_for([], BODY) == []


def test_a_precise_span_beats_the_paragraph_region():
    """The semicolon must not disappear under a highlight of the whole
    company paragraph - the precise mark is the useful one."""
    body = "One thing; another thing."
    notes = ["hook shares no distinctive term with the cited source", "contains a semicolon"]
    quotes = [h["quote"] for h in highlights_for(notes, body)]
    assert quotes[1] == ";"
    assert quotes[0] is None
