"""`normalise_paragraphs` repairs the model's paragraph breaks and nothing
else - see the module docstring for the live finding that motivated it."""

from app.domain.layout import normalise_paragraphs
from app.domain.validation import MIN_PARAGRAPHS, _paragraphs

HOOK = ("When a blue-collar borrower in a joint liability group misses a payment, "
        "does Kosh isolate that person immediately or wait for the group to self-regulate?")
SELF = "I built WaveWatch, which cut manual grading time by 70%, and have my resume attached."
ASK = "Would you take on a software engineering intern?"


def test_flat_block_is_split_into_hook_self_and_ask():
    out = normalise_paragraphs(f"{HOOK} {SELF} {ASK}")
    assert out == f"{HOOK}\n\n{SELF}\n\n{ASK}"
    assert len(_paragraphs(out)) >= MIN_PARAGRAPHS


def test_single_newlines_become_paragraph_breaks():
    assert normalise_paragraphs(f"{HOOK}\n{SELF}\n{ASK}") == f"{HOOK}\n\n{SELF}\n\n{ASK}"


def test_already_separated_body_is_untouched_except_whitespace():
    body = f"{HOOK}\n\n{SELF}\n\n{ASK}"
    assert normalise_paragraphs(f"  {body}\n") == body


def test_contractions_count_as_the_self_line():
    out = normalise_paragraphs(f"{HOOK} I'm a student, resume attached. {ASK}")
    assert out.split("\n\n") == [HOOK, "I'm a student, resume attached.", ASK]


def test_a_two_sentence_hook_stays_together():
    hook2 = f"{HOOK} That boundary is what I keep thinking about."
    out = normalise_paragraphs(f"{hook2} {SELF} {ASK}")
    assert out.split("\n\n")[0] == hook2


def test_no_self_sentence_means_no_guess():
    body = "One. Two. Three."
    assert normalise_paragraphs(body) == body


def test_never_changes_the_words():
    body = f"{HOOK} {SELF} {ASK}"
    assert normalise_paragraphs(body).replace("\n\n", " ") == body


def test_crlf_input_is_normalised():
    assert normalise_paragraphs(f"{HOOK}\r\n\r\n{SELF}\r\n\r\n{ASK}") == f"{HOOK}\n\n{SELF}\n\n{ASK}"
