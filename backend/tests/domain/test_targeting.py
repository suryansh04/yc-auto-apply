import pytest
from app.domain.targeting import is_target_company

def co(**kw):
    base = {"regions": ["India", "South Asia"], "all_locations": "Bengaluru, KA, India",
            "status": "Active", "team_size": 60}
    base.update(kw)
    return base

@pytest.mark.parametrize("team_size,expected", [
    (1, True), (10, True), (11, True), (200, True), (201, False), (0, False),
])
def test_team_size_band_is_1_to_200(team_size, expected):
    assert is_target_company(co(team_size=team_size)) is expected

def test_requires_india_in_regions_or_locations():
    assert is_target_company(co(regions=["United States of America"],
                                all_locations="Boston, MA, USA")) is False
    assert is_target_company(co(regions=[], all_locations="Hyderabad, India")) is True

def test_requires_active_status():
    assert is_target_company(co(status="Acquired")) is False
    assert is_target_company(co(status="Inactive")) is False

def test_missing_or_non_int_team_size_is_excluded():
    assert is_target_company(co(team_size=None)) is False
    assert is_target_company(co(team_size="60")) is False

def test_swipe_is_in_the_target_set():
    # The reference case for D24 - zero postings, still a valid target.
    swipe = co(team_size=60, all_locations="Hyderabad, Telangana, India")
    assert is_target_company(swipe) is True
