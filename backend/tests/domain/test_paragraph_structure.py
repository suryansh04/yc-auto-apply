"""Live finding (2026-09-13): a Cashfree draft passed validation as one
3-sentence block — hook, pitch and ask run together. The rules capped
paragraphs at 3 but never required more than one, so the spec's own "three
parts, each its own paragraph" structure was unenforced and the worst-reading
failure mode was the one that slipped through."""

import pytest

from app.domain.entities import Draft
from app.domain.validation import MIN_PARAGRAPHS, validate_style

HOOK = "Cashfree's Auto Collect matches inbound payments to customers."
PITCH = "I built Lerno.ai and cut generation time from 8 minutes to under 3."
ASK = "Would you take on a software engineering intern?"


def _draft(body: str) -> Draft:
    return Draft(subject="Cashfree internship inquiry", body=body,
                 research_source_type="news", research_source_id=1)


def test_min_paragraphs_is_two():
    assert MIN_PARAGRAPHS == 2


def test_one_block_containing_all_three_parts_is_rejected():
    errs = validate_style(_draft(f"{HOOK} {PITCH} {ASK}"))
    assert any("paragraph" in e for e in errs)


def test_three_paragraphs_pass():
    assert validate_style(_draft(f"{HOOK}\n\n{PITCH}\n\n{ASK}")) == []


def test_two_paragraphs_pass():
    assert validate_style(_draft(f"{HOOK}\n\n{PITCH} {ASK}")) == []


@pytest.mark.parametrize("sep", ["\n\n", "\r\n\r\n", "\n \n"])
def test_blank_line_forms_are_all_recognised(sep):
    assert validate_style(_draft(f"{HOOK}{sep}{PITCH}{sep}{ASK}")) == []


def test_single_newlines_do_not_count_as_paragraphs():
    errs = validate_style(_draft(f"{HOOK}\n{PITCH}\n{ASK}"))
    assert any("paragraph" in e for e in errs)
