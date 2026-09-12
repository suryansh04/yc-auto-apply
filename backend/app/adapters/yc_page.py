"""Parser + live source for a YC company page (spec §3/§9, Task 11).

YC's company pages ship their initial data as a single HTML-escaped JSON
blob in a `data-page` attribute (an Inertia.js convention) rather than as
separate API responses. `parse_company_page` extracts that blob and reshapes
it into the flat dict this pipeline persists.

Two fields matter downstream and are called out because a wrong value here
degrades — rather than crashes — a later stage:
- `job_postings[].employment_type` (from the payload's `type`) decides
  whether a role is intern-appropriate (D23).
- `company_launches[].published_email`, parsed out of the launch body, lets
  the pipeline skip email verification for that founder (D18).
- `company_news[].source` feeds the D29 "don't name the outlet" validator
  rule; derived from the title's trailing " - Outlet" convention (see
  `progress.md` P2 — accepted as specified, degrades to a weaker check
  rather than a false accept if a headline itself contains " - ").
"""

import html as html_lib
import json
import re
import time

import httpx

_DATA_PAGE = re.compile(r'data-page="([^"]+)"')
_MAILTO = re.compile(r"mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")

_YC_COMPANY_PAGE_URL = "https://www.ycombinator.com/companies/{slug}"
_REQUEST_INTERVAL_SECONDS = 1.0


def extract_published_email(body: str | None) -> str | None:
    if not body:
        return None
    m = _MAILTO.search(body)
    return m.group(1) if m else None


def parse_company_page(page_html: str) -> dict:
    m = _DATA_PAGE.search(page_html)
    if not m:
        raise ValueError("no data-page attribute found")
    props = json.loads(html_lib.unescape(m.group(1)))["props"]
    company = props.get("company") or {}

    return {
        "founders": [
            {"yc_user_id": f.get("user_id"), "full_name": f.get("full_name"),
             "title": f.get("title"), "linkedin_url": f.get("linkedin_url"),
             "twitter_url": f.get("twitter_url"), "bio": f.get("founder_bio")}
            for f in (company.get("founders") or [])
        ],
        "job_postings": [
            {"title": j.get("title"), "employment_type": j.get("type"),
             "min_experience": j.get("minExperience"), "location": j.get("location"),
             "url": j.get("url")}
            for j in (props.get("jobPostings") or [])
        ],
        "news": [
            {"title": n.get("title"), "url": n.get("url"),
             "source": (n.get("title") or "").rsplit(" - ", 1)[-1] if " - " in (n.get("title") or "") else None,
             "published_at": n.get("date")}
            for n in (props.get("newsItems") or [])
        ],
        "launches": [
            {"title": l.get("title"), "body": l.get("body"),
             "published_email": extract_published_email(l.get("body"))}
            for l in (props.get("launches") or [])
        ],
    }


class YCPageSource:
    """Real `CompanyPageSource` adapter: fetches a company's YC page over
    HTTP and returns it already parsed into `parse_company_page`'s dict
    shape (matching the `app.ports.CompanyPageSource.fetch_page` contract,
    which documents its return value as "the parsed `props`").

    Rate-limited to one request per second (per company page fetched by
    this instance) to stay a polite client against ycombinator.com.

    Fix round 1 (I2): `_now`/`_sleep` are injectable, defaulting to
    `time.monotonic`/`time.sleep`. Nothing about the throttle logic itself
    needs a real clock — the tests in `test_yc_page.py` pass a fake pair so
    `_throttle` can be pinned (does it wait the right amount? does it wait
    at all once the interval has already elapsed?) without an offline test
    suite actually sleeping for real.
    """

    def __init__(
        self,
        timeout: float = 30,
        min_interval: float = _REQUEST_INTERVAL_SECONDS,
        _now=time.monotonic,
        _sleep=time.sleep,
    ):
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request_at: float | None = None
        self._now = _now
        self._sleep = _sleep

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._now() - self._last_request_at
        remaining = self._min_interval - elapsed
        if remaining > 0:
            self._sleep(remaining)

    def fetch_page(self, slug: str) -> dict:
        self._throttle()
        response = httpx.get(
            _YC_COMPANY_PAGE_URL.format(slug=slug),
            timeout=self._timeout,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        )
        self._last_request_at = self._now()
        response.raise_for_status()
        return parse_company_page(response.text)
