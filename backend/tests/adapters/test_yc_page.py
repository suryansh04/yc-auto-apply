"""Task 11: parsing a YC company page's embedded `data-page` JSON blob
(spec §3/§9). `parse_company_page` is pure — no network, no DB — so this
test runs entirely offline against a real, committed page capture
(`tests/fixtures/vahan_page.html`, captured once per the brief's Step 1).
"""

from pathlib import Path

import pytest

from app.adapters.yc_page import parse_company_page

HTML = (Path(__file__).parent.parent / "fixtures" / "vahan_page.html").read_text(encoding="utf-8")


def test_extracts_founders_with_names_and_linkedin():
    parsed = parse_company_page(HTML)
    assert parsed["founders"], "expected at least one founder"
    f = parsed["founders"][0]
    assert f["full_name"] and f["linkedin_url"]


def test_extracts_news_items_with_source_and_url():
    parsed = parse_company_page(HTML)
    assert parsed["news"], "Vahan had news items when the fixture was captured"
    assert parsed["news"][0]["url"].startswith("http")


def test_extracts_job_postings_with_employment_type():
    parsed = parse_company_page(HTML)
    for p in parsed["job_postings"]:
        assert "employment_type" in p


# Fix round 1 (I1): the tests above only assert the key is present, so a
# swapped field in the parser's dict comprehension (e.g. `j.get("role")`
# instead of `j.get("type")`) would still pass the whole suite —
# `test_scrape_company_page.py`'s value tests feed a hand-built dict where
# `employment_type`/`source` are already pre-computed, so they only pin
# pass-through, not the adapter's own mapping. These pin the real mapping
# against the fixture's actual, hand-verified values: every one of Vahan's
# 4 job postings is "Full-time" and every one of its 3 news items is
# sourced "The Economic Times" when the fixture was captured.
def test_job_postings_employment_type_matches_the_fixtures_payload_type():
    parsed = parse_company_page(HTML)
    assert parsed["job_postings"], "expected at least one job posting"
    assert all(p["employment_type"] == "Full-time" for p in parsed["job_postings"])


def test_news_source_is_derived_from_the_titles_trailing_outlet():
    parsed = parse_company_page(HTML)
    assert parsed["news"], "expected at least one news item"
    assert all(n["source"] == "The Economic Times" for n in parsed["news"])


def test_extracts_published_email_from_launch_body():
    body = "reach me at [madhav@vahan.ai](mailto:madhav@vahan.ai)"
    from app.adapters.yc_page import extract_published_email
    assert extract_published_email(body) == "madhav@vahan.ai"


def test_returns_empty_lists_when_arrays_absent():
    minimal = '<div data-page="{&quot;props&quot;:{&quot;company&quot;:{&quot;founders&quot;:[]}}}"></div>'
    parsed = parse_company_page(minimal)
    assert parsed == {"founders": [], "job_postings": [], "news": [], "launches": []}


# Fix round 1 (I2): the rate limiter (`YCPageSource._throttle`) had zero
# test coverage — the offline suite never noticed because fakes have no
# delay. These inject a fake clock/sleep pair (no real waiting) to pin the
# actual throttle behaviour: it waits out the remaining interval when a
# request would otherwise land too soon, it does not wait once the interval
# has already elapsed, and it never waits before the very first request.
def test_throttle_waits_for_the_remaining_interval_since_the_last_request():
    from app.adapters.yc_page import YCPageSource

    clock = {"t": 100.0}
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        clock["t"] += seconds

    source = YCPageSource(_now=lambda: clock["t"], _sleep=fake_sleep)
    source._last_request_at = 100.0
    clock["t"] = 100.4  # only 0.4s elapsed since the last request

    source._throttle()

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.6)  # 1.0s min interval - 0.4s elapsed


def test_throttle_does_not_wait_once_the_interval_has_elapsed():
    from app.adapters.yc_page import YCPageSource

    clock = {"t": 100.0}
    sleep_calls = []

    source = YCPageSource(_now=lambda: clock["t"], _sleep=lambda s: sleep_calls.append(s))
    source._last_request_at = 100.0
    clock["t"] = 101.5  # 1.5s elapsed, past the 1.0s min interval

    source._throttle()

    assert sleep_calls == []


def test_throttle_does_not_wait_before_the_first_request():
    from app.adapters.yc_page import YCPageSource

    sleep_calls = []
    source = YCPageSource(_sleep=lambda s: sleep_calls.append(s))

    source._throttle()

    assert sleep_calls == []
