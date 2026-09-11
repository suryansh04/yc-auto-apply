"""Task 11: scrape founders and research material from each company's YC
page (spec §3/§9).

`ScrapeCompanyPage` is pure application logic: it depends only on the
`CompanyPageSource` port and a small bundle of repos (`ScrapeRepos`), both
injected by the caller (D8). It scrapes exactly one company per `run()` call
(matching the brief's `ScrapeCompanyPage(source, repos).run(company)`
signature); the CLI command is responsible for listing companies that need
a scrape and calling `run()` once per company at the source's own rate
limit (see `app.adapters.yc_page.YCPageSource`, which self-throttles to
1 request/second).

Three persisted fields matter downstream and are handled deliberately here:
- `job_postings.employment_type` is passed straight through from the
  parser's `employment_type` (itself the payload's `type`) — D23 depends on
  it to decide whether a role is intern-appropriate.
- `company_launches.published_email`, already parsed by the adapter, is
  passed straight through — D18 uses it to skip email verification for that
  founder.
- `company_news.published_at` arrives from the adapter as a raw string
  (e.g. "May 04, 2025" — the task-11 brief's own `parse_company_page` does
  not parse it, and its tests don't require that it does). The
  `company_news` table's column is a real `DateTime`, so this layer parses
  it into a `datetime` before persisting; a date string that doesn't match
  the observed format is dropped to `None` rather than raising, since a
  malformed date shouldn't abort the whole company's scrape.

A new founder (one with no prior `Founder` row for its `yc_user_id`) gets an
`Outreach` row created in `NEW`. An already-known founder's `Outreach` row
is never touched here — re-scraping must not reset a founder's outreach
state machine (spec's state transitions are one-directional and guarded;
resetting to NEW on every re-scrape would silently undo real progress).
A failure reaching or parsing the page is recorded on the returned
`ScrapeResult` rather than raised, so one bad company doesn't stop a batch
CLI run over the rest.

Fix round 1 (Critical): a failure raised mid-persist (e.g. a scraped
founder with a null `yc_user_id` hitting a NOT NULL/unique constraint at
`flush()`) used to be swallowed into `result.error` without rolling back
the session. The CLI reuses one `Session` across every company in the
batch and calls `session.commit()` after every `run()`, including a failed
one; committing a session SQLAlchemy has already marked
rollback-required raises `PendingRollbackError`, uncaught, which aborted
every company after the failing one with a raw traceback — the exact
"one bad company doesn't stop the rest" guarantee this docstring claims,
broken for any failure past `fetch_page`. `ScrapeRepos` now carries the
`session` this use case's repos share, and the persist `except` branch
rolls it back before returning — the use case owns the transaction
boundary it just violated, so it's also the one that repairs it, rather
than pushing that responsibility onto every caller of `run()`.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.domain.states import OutreachState

_NEWS_DATE_FORMAT = "%b %d, %Y"  # e.g. "May 04, 2025"


def _parse_news_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _NEWS_DATE_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class ScrapeResult:
    company_slug: str
    founders: int = 0
    new_founders: int = 0
    job_postings: int = 0
    news: int = 0
    launches: int = 0
    error: str | None = None


class _UpsertRepo(Protocol):
    def upsert(self, **fields): ...


class _RollbackCapable(Protocol):
    def rollback(self) -> None: ...


@dataclass
class ScrapeRepos:
    founder: _UpsertRepo          # + get_by_yc_user_id(yc_user_id)
    job_posting: _UpsertRepo
    news: _UpsertRepo
    launch: _UpsertRepo
    outreach: _UpsertRepo         # + get_by_founder_id(founder_id)
    session: _RollbackCapable     # the session all five repos above share


class ScrapeCompanyPage:
    def __init__(self, source, repos: ScrapeRepos):
        self._source = source
        self._repos = repos

    def run(self, company) -> ScrapeResult:
        result = ScrapeResult(company_slug=company.slug)

        try:
            parsed = self._source.fetch_page(company.slug)
        except Exception as exc:
            result.error = f"{company.slug}: {exc}"
            return result

        try:
            for raw in parsed.get("founders") or []:
                is_new = self._repos.founder.get_by_yc_user_id(raw["yc_user_id"]) is None
                founder = self._repos.founder.upsert(
                    company_id=company.id,
                    yc_user_id=raw["yc_user_id"],
                    full_name=raw.get("full_name"),
                    title=raw.get("title"),
                    linkedin_url=raw.get("linkedin_url"),
                    twitter_url=raw.get("twitter_url"),
                    bio=raw.get("bio"),
                )
                result.founders += 1
                if is_new:
                    result.new_founders += 1
                    if self._repos.outreach.get_by_founder_id(founder.id) is None:
                        self._repos.outreach.upsert(
                            founder_id=founder.id, state=str(OutreachState.NEW)
                        )

            for raw in parsed.get("job_postings") or []:
                self._repos.job_posting.upsert(
                    company_id=company.id,
                    title=raw.get("title"),
                    employment_type=raw.get("employment_type"),
                    min_experience=raw.get("min_experience"),
                    location=raw.get("location"),
                    url=raw.get("url"),
                )
                result.job_postings += 1

            for raw in parsed.get("news") or []:
                self._repos.news.upsert(
                    company_id=company.id,
                    title=raw.get("title"),
                    url=raw.get("url"),
                    source=raw.get("source"),
                    published_at=_parse_news_date(raw.get("published_at")),
                )
                result.news += 1

            for raw in parsed.get("launches") or []:
                self._repos.launch.upsert(
                    company_id=company.id,
                    title=raw.get("title"),
                    body=raw.get("body"),
                    published_email=raw.get("published_email"),
                )
                result.launches += 1
        except Exception as exc:
            result.error = f"{company.slug}: {exc}"
            self._repos.session.rollback()

        return result
