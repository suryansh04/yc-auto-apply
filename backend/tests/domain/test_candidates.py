from app.domain.names import normalise_name
from app.domain.candidates import generate_candidates

def test_normalise_strips_diacritics_and_case():
    assert normalise_name("José Ángel") == ("jose", "angel")

def test_three_part_indian_name_uses_first_and_last_token():
    assert normalise_name("Suryansh Pratap Singh") == ("suryansh", "singh")

def test_mononym_has_empty_last():
    assert normalise_name("Madhav") == ("madhav", "")

def test_candidate_order_matches_spec_priority():
    got = [c.address for c in generate_candidates("Pratish Gandhi", "onefin.com")]
    assert got == [
        "pratish@onefin.com",
        "pratish.gandhi@onefin.com",
        "pgandhi@onefin.com",
        "pratishgandhi@onefin.com",
        "pratish_gandhi@onefin.com",
        "pratishg@onefin.com",
        "gandhi@onefin.com",
    ]

def test_priorities_are_sequential_from_one():
    cands = generate_candidates("Pratish Gandhi", "onefin.com")
    assert [c.priority for c in cands] == [1, 2, 3, 4, 5, 6, 7]

def test_mononym_produces_only_first_patterns():
    got = [c.address for c in generate_candidates("Madhav", "vahan.ai")]
    assert got == ["madhav@vahan.ai"]

def test_no_duplicate_addresses():
    cands = generate_candidates("Ram Ram", "x.com")
    assert len({c.address for c in cands}) == len(cands)
