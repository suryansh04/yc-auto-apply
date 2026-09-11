"""Task 11: parsing a YC company page's embedded `data-page` JSON blob
(spec §3/§9). `parse_company_page` is pure — no network, no DB — so this
test runs entirely offline against a real, committed page capture
(`tests/fixtures/vahan_page.html`, captured once per the brief's Step 1).
"""

from pathlib import Path

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


def test_extracts_published_email_from_launch_body():
    body = "reach me at [madhav@vahan.ai](mailto:madhav@vahan.ai)"
    from app.adapters.yc_page import extract_published_email
    assert extract_published_email(body) == "madhav@vahan.ai"


def test_returns_empty_lists_when_arrays_absent():
    minimal = '<div data-page="{&quot;props&quot;:{&quot;company&quot;:{&quot;founders&quot;:[]}}}"></div>'
    parsed = parse_company_page(minimal)
    assert parsed == {"founders": [], "job_postings": [], "news": [], "launches": []}
