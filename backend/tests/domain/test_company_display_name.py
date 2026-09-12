"""Live finding (2026-09-13): `validate_grounding` demanded the literal
`company_name` in the body, so a company stored as "Wifi Dabba, Inc." forced
the drafter to write "Wifi Dabba, Inc." mid-sentence — the one thing in an
otherwise good first draft that read as robotic. The display name strips
legal suffixes; prompt and validator both use it."""

import pytest

from app.domain.names import company_display_name


@pytest.mark.parametrize("raw, expected", [
    ("Wifi Dabba, Inc.", "Wifi Dabba"),
    ("Wifi Dabba Inc", "Wifi Dabba"),
    ("Vahan Inc.", "Vahan"),
    ("OkCredit Pvt. Ltd.", "OkCredit"),
    ("Swipe Private Limited", "Swipe"),
    ("Dost Education LLC", "Dost Education"),
    ("BulkMRO Ltd", "BulkMRO"),
    ("Cashfree Payments", "Cashfree Payments"),   # no suffix: unchanged
    ("Incredible Health", "Incredible Health"),   # "Inc" only as a word boundary
    ("  Edyst  ", "Edyst"),
    ("", ""),
])
def test_company_display_name_strips_legal_suffixes(raw, expected):
    assert company_display_name(raw) == expected


def test_validator_accepts_the_display_name_in_the_body():
    from app.domain.entities import Draft
    from app.domain.validation import SourceRow, validate_grounding

    src = SourceRow(kind="long_description", text="We beam internet over free-space optics lasers to towns.", publication=None, intern_appropriate=True)
    draft = Draft(subject="Wifi Dabba software intern", body=(
        "Beaming internet over free-space optics at Wifi Dabba sidesteps trenching fiber.\n\n"
        "I built a scheduler in Python.\n\nWould you take a software engineering intern?"),
        research_source_type="long_description", research_source_id=1)
    errs = validate_grounding(draft, src, "Wifi Dabba, Inc.", "Decentralised broadband.", [])
    assert "does not name the company" not in errs
