from dataclasses import dataclass
from datetime import datetime


@dataclass
class Company:
    yc_id: int
    slug: str
    name: str
    website: str | None
    domain: str | None
    batch: str | None
    team_size: int | None
    status: str | None
    is_hiring: bool
    one_liner: str | None
    long_description: str | None
    last_synced_at: datetime | None = None   # final fix wave (F12): stamped by SyncCompanies


@dataclass
class Founder:
    yc_user_id: int
    company_id: int
    full_name: str
    title: str | None
    linkedin_url: str | None
    twitter_url: str | None
    bio: str | None


@dataclass
class JobPosting:
    company_id: int
    title: str
    employment_type: str | None
    min_experience: str | None
    location: str | None
    url: str | None


@dataclass
class NewsItem:
    company_id: int
    title: str
    url: str
    source: str | None
    published_at: datetime | None


@dataclass
class LaunchPost:
    company_id: int
    title: str
    body: str
    published_email: str | None


@dataclass
class ResumeClaim:
    kind: str          # skill | project | metric | role | education
    text: str
    source_span: str
    # Task 13: False when `source_span` could not be found verbatim in the
    # resume text — the audit trail (spec §10) is broken for that claim and
    # the operator should read it with suspicion. Defaults True so every
    # pre-existing `ResumeClaim(kind, text, source_span)` call site is
    # untouched.
    span_verified: bool = True


@dataclass
class Draft:
    subject: str
    body: str
    research_source_type: str        # news | launch | job_posting | long_description
    research_source_id: int | None
    sequence_step: int = 0
    # 2026-09-25: the line about the founder, when one was written. It is
    # already in `body`; this is what lets a later swap find it.
    founder_line: str | None = None
