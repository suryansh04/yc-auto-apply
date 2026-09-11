"""Task 15, ruling R2: the greeting, signature and opt-out line are composed
at send time by a pure function; the stored body is never touched."""

import pytest

from app.domain.compose import OPT_OUT_LINE, compose_wire_text, first_name

SIGNATURE = "Suryansh\nCS, final year"
BODY = (
    "You're matching blue-collar workers to jobs over voice.\n\n"
    "I built a scheduler in Python.\n\n"
    "Would you take an intern this summer?"
)


def test_exact_wire_text_shape():
    out = compose_wire_text("Madhav", BODY, SIGNATURE, OPT_OUT_LINE)
    assert out == f"Hi Madhav,\n\n{BODY}\n\n{SIGNATURE}\n\n{OPT_OUT_LINE}"


def test_opt_out_line_is_the_default_fourth_part():
    assert compose_wire_text("Madhav", BODY, SIGNATURE) == compose_wire_text(
        "Madhav", BODY, SIGNATURE, OPT_OUT_LINE
    )


def test_opt_out_line_is_a_single_line_and_offers_a_way_out():
    assert "\n" not in OPT_OUT_LINE
    assert OPT_OUT_LINE.strip() == OPT_OUT_LINE
    assert "reply" in OPT_OUT_LINE.lower()
    assert "follow up" in OPT_OUT_LINE.lower()


def test_stored_body_is_used_verbatim_in_the_middle():
    out = compose_wire_text("Riya", BODY, SIGNATURE)
    assert BODY in out
    assert out.startswith("Hi Riya,\n\n")
    assert out.endswith("\n\n" + OPT_OUT_LINE)


def test_body_and_signature_surrounding_whitespace_does_not_double_blank_lines():
    out = compose_wire_text("Riya", BODY + "\n", "\n" + SIGNATURE + "\n")
    assert out == f"Hi Riya,\n\n{BODY}\n\n{SIGNATURE}\n\n{OPT_OUT_LINE}"
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
    ],
)
def test_first_name_is_the_first_whitespace_token(full_name, expected):
    assert first_name(full_name) == expected


@pytest.mark.parametrize("full_name", ["", "   ", None])
def test_first_name_falls_back_to_there_for_a_blank_name(full_name):
    assert first_name(full_name) == "there"
