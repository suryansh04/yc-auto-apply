"""Task 15, ruling R2: the greeting and signature are composed at send
time by a pure function; the stored body is never touched.

2026-09-23: the opt-out line was deleted at the operator's instruction,
so the fourth part is empty and the wire text now ends at the signature.
The tests that pinned its wording are gone; the one that pins its absence
is `test_there_is_no_opt_out_line`."""

import pytest

from app.domain.compose import OPT_OUT_LINE, compose_wire_text, first_name

SIGNATURE = "Suryansh\nCS, final year"
BODY = (
    "You're matching blue-collar workers to jobs over voice.\n\n"
    "I built a scheduler in Python.\n\n"
    "Would you take an intern this summer?"
)


def test_exact_wire_text_shape():
    out = compose_wire_text("Madhav", BODY, SIGNATURE)
    assert out == f"Hi Madhav,\n\n{BODY}\n\n{SIGNATURE}"


def test_there_is_no_opt_out_line():
    # 2026-09-23, operator instruction: "delete this line from the
    # email". Spec 18 asks for one; the person whose name is on the
    # letter decided against it. Emptying the constant is the whole
    # change, and is what makes it reversible.
    assert OPT_OUT_LINE == ""


def test_an_empty_opt_out_line_leaves_no_trailing_blank_line():
    # `compose_wire_text` drops empty parts, so deleting the sentence
    # cannot leave the letter ending in whitespace.
    out = compose_wire_text("Madhav", BODY, SIGNATURE)
    assert out.endswith(SIGNATURE)
    assert not out.endswith("\n")


def test_a_caller_can_still_pass_a_closing_line():
    # The part was emptied, not removed: putting a sentence back is one
    # string in `app.domain.compose`.
    out = compose_wire_text("Madhav", BODY, SIGNATURE, "One word is enough.")
    assert out == f"Hi Madhav,\n\n{BODY}\n\n{SIGNATURE}\n\nOne word is enough."


def test_stored_body_is_used_verbatim_in_the_middle():
    out = compose_wire_text("Riya", BODY, SIGNATURE)
    assert BODY in out
    assert out.startswith("Hi Riya,\n\n")
    assert out.endswith(SIGNATURE)


def test_body_and_signature_surrounding_whitespace_does_not_double_blank_lines():
    out = compose_wire_text("Riya", BODY + "\n", "\n" + SIGNATURE + "\n")
    assert out == f"Hi Riya,\n\n{BODY}\n\n{SIGNATURE}"
    assert "\n\n\n" not in out


@pytest.mark.parametrize("signature", ["", "   ", "\n\t\n"])
def test_empty_or_whitespace_signature_drops_the_part_instead_of_a_triple_blank_line(signature):
    """Fix round 1 (F2): Task 16's preview may compose before SIGNATURE is
    configured; the CLI guard, not this function, is what makes a real
    send require one."""
    out = compose_wire_text("Riya", BODY, signature)
    assert out == f"Hi Riya,\n\n{BODY}"
    assert "\n\n\n" not in out


def test_no_html_and_no_tracking_in_the_wire_text():
    out = compose_wire_text("Riya", BODY, SIGNATURE)
    assert "<" not in out and ">" not in out  # D10: plain text only


@pytest.mark.parametrize(
    "full_name, expected",
    [
        ("Madhav Krishna", "Madhav"),
        ("  Riya   Nair ", "Riya"),
        ("Pratish", "Pratish"),
        ("Jean-Luc Picard", "Jean-Luc"),
        ("Dr. Asha Rao", "Asha"),          # fix round 1 (F9): honorifics skipped
        ("Mr. Vikram Shah", "Vikram"),
        ("Ms. Riya Nair", "Riya"),
        ("Prof. Dr. Jane Founder", "Jane"),
    ],
)
def test_first_name_is_the_first_non_honorific_whitespace_token(full_name, expected):
    assert first_name(full_name) == expected


@pytest.mark.parametrize("full_name", ["", "   ", None, "Dr.", "Mr. Dr."])
def test_first_name_falls_back_to_there_for_a_blank_name(full_name):
    assert first_name(full_name) == "there"
