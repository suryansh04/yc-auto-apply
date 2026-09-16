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


# --- Live finding 2 (2026-09-13): the naming check was too literal ------------
# Sarvam wrote a good Cashfree email saying "Cashfree" throughout and was
# rejected with "does not name the company" because the stored name is
# "Cashfree Payments". A reader would say the email names the company. The
# check now accepts the distinctive leading part of the name.

import pytest as _pytest

from app.domain.names import company_name_variants


@_pytest.mark.parametrize("stored, written, ok", [
    ("Cashfree Payments", "Cashfree ships virtual accounts.", True),
    ("Wifi Dabba, Inc.", "Wifi Dabba beams internet.", True),
    ("Dost Education", "Dost Education calls parents.", True),
    ("Dost Education", "Dost reaches parents by phone.", True),
    ("AlgoSurg Inc", "AlgoSurg plans surgeries.", True),
    ("OkCredit", "OkCredit tracks ledgers.", True),
    ("Orangewood Labs", "Orangewood arms are programmable.", True),
    # a generic leading word must NOT count on its own
    ("The Better Company", "Better things happen.", False),
    ("Cashfree Payments", "Payments are hard.", False),
    ("Mudrex", "Crypto bots are neat.", False),
])
def test_naming_accepts_the_distinctive_part_of_the_name(stored, written, ok):
    from app.domain.entities import Draft
    from app.domain.validation import SourceRow, validate_grounding

    src = SourceRow(kind="long_description", text=written, publication=None, intern_appropriate=True)
    draft = Draft(subject="x y z", body=f"{written}\n\nI built a scheduler.\n\nWould you take an intern?",
                  research_source_type="long_description", research_source_id=1)
    errs = validate_grounding(draft, src, stored, "one liner", [])
    assert ("does not name the company" in errs) is (not ok)


def test_company_name_variants_are_ordered_longest_first():
    assert company_name_variants("Cashfree Payments") == ["Cashfree Payments", "Cashfree"]
    assert company_name_variants("Mudrex") == ["Mudrex"]
    assert company_name_variants("The Better Company") == ["The Better Company"]


def test_trailing_period_in_the_stored_name_is_not_required_in_the_body():
    # YC stores "Statiq." - a draft saying "does Statiq block the payment"
    # names the company (live, 2026-09-16).
    assert company_name_variants("Statiq.") == ["Statiq"]
