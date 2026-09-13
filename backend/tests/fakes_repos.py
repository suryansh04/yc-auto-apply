"""In-memory repo fakes shared by the application-layer tests, the
review-loop CLI tests and the offline end-to-end test (final fix wave, F9 /
ruling FR-5).

Plain module, not a conftest: the fixtures that build these live in
`tests/conftest.py` (the `repos` bundle and the fixtures root-level tests
share) and `tests/application/conftest.py` (application-only founders).
Neither conftest imports the other - the old root conftest re-exported
fixtures by importing `tests.application.conftest` as a module, which is
what made pytest lose the application fixtures on an interleaved
invocation such as
`pytest tests/application/test_resolve_emails.py tests/test_cli.py
tests/application/test_draft_outreach.py` (`fixture 'repos' not found`).

These fakes are deliberately separate from `tests/fakes.py`: that module
holds fakes for the *port* Protocols in `app.ports` (`EmailVerifier`,
`Drafter`, ...), each satisfying a fixed interface pinned by
`test_fakes_satisfy_ports.py`. The repos bundled into `ResolveRepos` /
`DraftRepos` are not ports - they are each application module's own
dependency shape. `founder`/`outreach` are wired to share state (a fake
join, mirroring the real `FounderRepo.list_by_state`'s SQL join to
`Outreach.state`) since one `Outreach` row exists per founder.

`FakeRepos` is a structural superset of `ResolveRepos` (Task 12),
`DraftRepos` (Task 14), `SendRepos` (Task 15), `ReviewRepos` (Task 16),
`ManualEmailRepos` (final fix wave) and - since the offline end-to-end
test - the `SyncCompanies` company repo and `ScrapeRepos` too; each use
case only ever touches the attributes it declares.

Deliberate fixture choice: each fake `Outreach` row's `id` is set equal to
its `founder_id` (both come from the same fixture-local counter). Nothing
in the use cases relies on this - they only ever look outreach rows up via
`get_by_founder_id`/`upsert(founder_id=...)` - but the briefs' own test
bodies call `repos.outreach.get(a_founder.id)`, i.e. address the outreach
row by the *founder's* id. Matching the two id spaces is what makes that
call resolve to the right row.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


from app.domain.states import OutreachState


@dataclass
class FakeCompanyRow:
    id: int
    domain: str | None = None
    name: str = ""
    one_liner: str | None = None
    long_description: str | None = None
    # Task 16 (offline e2e): the columns `SyncCompanies.run` writes and
    # `cli.scrape` / `ScrapeCompanyPage` read.
    yc_id: int | None = None
    slug: str = ""
    website: str | None = None
    batch: str | None = None
    team_size: int | None = None
    status: str | None = None
    is_hiring: bool = False
    last_synced_at: object = None   # final fix wave (F12): stamped via the injected Clock port


class FakeCompanyRepo:
    def __init__(self):
        self.rows: dict[int, FakeCompanyRow] = {}

    def add(self, id: int, domain: str | None = None, name: str = "",
            one_liner: str | None = None, long_description: str | None = None) -> FakeCompanyRow:
        row = FakeCompanyRow(id=id, domain=domain, name=name,
                             one_liner=one_liner, long_description=long_description)
        self.rows[id] = row
        return row

    def get(self, id: int) -> FakeCompanyRow | None:
        return self.rows.get(id)

    def upsert(self, **fields) -> FakeCompanyRow:
        """Task 16: mirrors `CompanyRepo.upsert` (keyed on `yc_id`)."""
        existing = next((r for r in self.rows.values() if r.yc_id == fields["yc_id"]), None)
        if existing is None:
            existing = FakeCompanyRow(id=len(self.rows) + 1, **fields)
            self.rows[existing.id] = existing
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        return existing

    def list_by_status(self, status: str) -> list[FakeCompanyRow]:
        return [r for r in self.rows.values() if r.status == status]


@dataclass
class FakeLaunchRow:
    company_id: int
    body: str = ""
    published_email: str | None = None
    id: int = 0
    title: str = ""


class FakeLaunchRepo:
    def __init__(self):
        self.rows: list[FakeLaunchRow] = []

    def add(self, company_id: int, body: str = "", published_email: str | None = None,
            id: int | None = None, title: str = "") -> FakeLaunchRow:
        row = FakeLaunchRow(company_id=company_id, body=body, published_email=published_email,
                            id=id if id is not None else len(self.rows) + 1, title=title)
        self.rows.append(row)
        return row

    def upsert(self, **fields) -> FakeLaunchRow:
        """Task 16: mirrors `CompanyLaunchRepo.upsert` (keyed on `(company_id, title)`)."""
        existing = next((r for r in self.rows if r.company_id == fields["company_id"]
                         and r.title == fields.get("title")), None)
        if existing is None:
            return self.add(**{k: (v if v is not None else "") if k in ("body", "title") else v
                               for k, v in fields.items()})
        for k, v in fields.items():
            setattr(existing, k, v)
        return existing

    def list_by_company_id(self, company_id: int) -> list[FakeLaunchRow]:
        return [r for r in self.rows if r.company_id == company_id]


@dataclass
class FakeOutreachRow:
    id: int
    founder_id: int
    state: str = str(OutreachState.NEEDS_EMAIL)
    resolved_email: str | None = None
    last_error: str | None = None
    sequence_step: int = 0
    next_action_at: object = None  # Task 15: stamped via the injected Clock port


class FakeOutreachRepo:
    def __init__(self):
        self.rows: dict[int, FakeOutreachRow] = {}
        self._by_founder_id: dict[int, int] = {}

    def add(self, id: int, founder_id: int, state: str) -> FakeOutreachRow:
        row = FakeOutreachRow(id=id, founder_id=founder_id, state=state)
        self.rows[id] = row
        self._by_founder_id[founder_id] = id
        return row

    def get(self, id: int) -> FakeOutreachRow | None:
        return self.rows.get(id)

    def get_by_founder_id(self, founder_id: int) -> FakeOutreachRow | None:
        row_id = self._by_founder_id.get(founder_id)
        return self.rows.get(row_id) if row_id is not None else None

    def upsert(self, **fields) -> FakeOutreachRow:
        founder_id = fields["founder_id"]
        row_id = self._by_founder_id.get(founder_id)
        if row_id is None:
            row_id = fields.get("id", founder_id)
            row = FakeOutreachRow(id=row_id, founder_id=founder_id)
            self.rows[row_id] = row
            self._by_founder_id[founder_id] = row_id
        row = self.rows[row_id]
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    def all(self) -> list[FakeOutreachRow]:
        return list(self.rows.values())


@dataclass
class FakeFounderRow:
    id: int
    company_id: int
    full_name: str
    linkedin_url: str | None = None
    title: str | None = None
    bio: str | None = None
    yc_user_id: int | None = None   # Task 16 (offline e2e): what `ScrapeCompanyPage` keys on
    twitter_url: str | None = None
    scraped_at: object = None       # final fix wave (F12): stamped via the injected Clock port


class FakeFounderRepo:
    """Mirrors the real `FounderRepo.list_by_state`, which joins to
    `Outreach.state` — this fake does the equivalent join against the
    `FakeOutreachRepo` it's constructed with."""

    def __init__(self, outreach_repo: FakeOutreachRepo):
        self._outreach = outreach_repo
        self.rows: dict[int, FakeFounderRow] = {}

    def add(self, id: int, company_id: int, full_name: str, linkedin_url: str | None = None,
            title: str | None = None, bio: str | None = None) -> FakeFounderRow:
        row = FakeFounderRow(id=id, company_id=company_id, full_name=full_name,
                             linkedin_url=linkedin_url, title=title, bio=bio)
        self.rows[id] = row
        return row

    def get(self, id: int) -> FakeFounderRow | None:
        return self.rows.get(id)

    def upsert(self, **fields) -> FakeFounderRow:
        """Task 16: mirrors `FounderRepo.upsert` (keyed on `yc_user_id`)."""
        existing = self.get_by_yc_user_id(fields["yc_user_id"])
        if existing is None:
            existing = FakeFounderRow(id=len(self.rows) + 1, **fields)
            self.rows[existing.id] = existing
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        return existing

    def get_by_yc_user_id(self, yc_user_id: int) -> FakeFounderRow | None:
        return next((r for r in self.rows.values() if r.yc_user_id == yc_user_id), None)

    def list_by_state(self, state) -> list[FakeFounderRow]:
        out = []
        for founder in self.rows.values():
            outreach = self._outreach.get_by_founder_id(founder.id)
            if outreach is not None and outreach.state == str(state):
                out.append(founder)
        return out

    def list_by_company_id(self, company_id: int) -> list[FakeFounderRow]:
        return [f for f in self.rows.values() if f.company_id == company_id]


@dataclass
class FakeEmailCandidateRow:
    founder_id: int
    address: str
    pattern: str | None = None
    priority: int | None = None
    verdict: str | None = None
    provider: str | None = None
    verified_at: object = None  # fix round 1: stamped via the injected Clock port


class FakeEmailCandidateRepo:
    def __init__(self):
        self.rows: dict[tuple[int, str], FakeEmailCandidateRow] = {}

    def upsert(self, **fields) -> FakeEmailCandidateRow:
        key = (fields["founder_id"], fields["address"])
        row = self.rows.get(key)
        if row is None:
            row = FakeEmailCandidateRow(**fields)
            self.rows[key] = row
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        return row

    def list_by_founder_id(self, founder_id: int) -> list[FakeEmailCandidateRow]:
        return [r for r in self.rows.values() if r.founder_id == founder_id]


# --- Task 14 fakes ------------------------------------------------------------


@dataclass
class FakeNewsRow:
    id: int
    company_id: int
    title: str
    source: str | None = None
    url: str | None = None          # Task 16 (offline e2e): the real repo's upsert key
    published_at: object = None


class FakeNewsRepo:
    def __init__(self):
        self.rows: list[FakeNewsRow] = []

    def add(self, id: int, company_id: int, title: str, source: str | None = None) -> FakeNewsRow:
        row = FakeNewsRow(id=id, company_id=company_id, title=title, source=source)
        self.rows.append(row)
        return row

    def upsert(self, **fields) -> FakeNewsRow:
        """Task 16: mirrors `CompanyNewsRepo.upsert` (keyed on `(company_id, url)`)."""
        existing = next((r for r in self.rows if r.company_id == fields["company_id"]
                         and r.url == fields.get("url")), None)
        if existing is None:
            existing = FakeNewsRow(id=len(self.rows) + 1, **fields)
            self.rows.append(existing)
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        return existing

    def list_by_company_id(self, company_id: int) -> list[FakeNewsRow]:
        return [r for r in self.rows if r.company_id == company_id]


@dataclass
class FakeJobPostingRow:
    id: int
    company_id: int
    title: str
    employment_type: str | None = None
    min_experience: str | None = None
    location: str | None = None     # Task 16 (offline e2e)
    url: str | None = None


class FakeJobPostingRepo:
    def __init__(self):
        self.rows: list[FakeJobPostingRow] = []

    def add(self, id: int, company_id: int, title: str, employment_type: str | None = None,
            min_experience: str | None = None) -> FakeJobPostingRow:
        row = FakeJobPostingRow(id=id, company_id=company_id, title=title,
                                employment_type=employment_type, min_experience=min_experience)
        self.rows.append(row)
        return row

    def upsert(self, **fields) -> FakeJobPostingRow:
        """Task 16: mirrors `JobPostingRepo.upsert` (keyed on `(company_id, url)`)."""
        existing = next((r for r in self.rows if r.company_id == fields["company_id"]
                         and r.url == fields.get("url")), None)
        if existing is None:
            existing = FakeJobPostingRow(id=len(self.rows) + 1, **fields)
            self.rows.append(existing)
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        return existing

    def list_by_company_id(self, company_id: int) -> list[FakeJobPostingRow]:
        return [r for r in self.rows if r.company_id == company_id]


class DuplicateOutboundMessage(Exception):
    """Stands in for the real `IntegrityError` raised by
    `uq_messages_outreach_step_outbound` (fix round 1, F1): a second
    outbound insert for the same `(outreach_id, sequence_step)`."""


@dataclass
class FakeMessageRow:
    id: int
    outreach_id: int
    sequence_step: int
    direction: str
    body: str
    subject: str | None = None
    research_source_type: str | None = None
    research_source_id: int | None = None
    approved_at: object = None
    edited_by_user: bool = False
    sent_at: object = None            # Task 15: set by `reserve_for_send`
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None


class FakeMessageRepo:
    """Mirrors `MessageRepo` including, since fix round 1, the schema's
    partial unique index on outbound `(outreach_id, sequence_step)`: an
    insert that would duplicate it raises, so a use case that forgets to
    upsert on the natural key fails here the way it would fail in Postgres."""

    def __init__(self):
        self.rows: list[FakeMessageRow] = []

    def upsert(self, **fields) -> FakeMessageRow:
        existing = None
        if fields.get("id") is not None:
            existing = next((r for r in self.rows if r.id == fields["id"]), None)
        if existing is None:
            if fields.get("direction") == "outbound" and self.get_outbound(
                fields["outreach_id"], fields["sequence_step"]
            ) is not None:
                raise DuplicateOutboundMessage(
                    f"outbound message for outreach {fields['outreach_id']} "
                    f"step {fields['sequence_step']} already exists"
                )
            existing = FakeMessageRow(id=len(self.rows) + 1, **{k: v for k, v in fields.items() if k != "id"})
            self.rows.append(existing)
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        return existing

    def get(self, id: int) -> FakeMessageRow | None:
        return next((r for r in self.rows if r.id == id), None)

    def get_outbound(self, outreach_id: int, sequence_step: int) -> FakeMessageRow | None:
        return next((r for r in self.rows
                     if r.outreach_id == outreach_id and r.sequence_step == sequence_step
                     and r.direction == "outbound"), None)

    def list_by_outreach_id(self, outreach_id: int) -> list[FakeMessageRow]:
        return [r for r in self.rows if r.outreach_id == outreach_id]

    def reserve_for_send(self, message_id: int, now) -> int:
        """Task 15 (ruling R1): models the real repo's single conditional
        `UPDATE messages SET sent_at = :now WHERE id = :id AND sent_at IS
        NULL AND approved_at IS NOT NULL` and returns its rowcount — 1 when
        this call won the reservation, 0 when the row is missing, already
        reserved, or not approved. The predicate lives here, not in the use
        case, so `test_crash_after_gmail_call_does_not_double_send` exercises
        the same semantics Postgres would enforce."""
        row = self.get(message_id)
        if row is None or row.sent_at is not None or row.approved_at is None:
            return 0
        row.sent_at = now
        return 1

    def count_sent_since(self, since) -> int:
        """Task 15: outbound rows with `sent_at >= since` — what the daily
        cap is counted against, so a re-run never exceeds it."""
        return sum(1 for r in self.rows
                   if r.direction == "outbound" and r.sent_at is not None and r.sent_at >= since)


@dataclass
class FakeResumeProfileRow:
    raw_text: str
    claims: list[dict]


class FakeResumeProfileRepo:
    def __init__(self):
        self.profile: FakeResumeProfileRow | None = None

    def save(self, raw_text: str, claims: list[dict]) -> FakeResumeProfileRow:
        self.profile = FakeResumeProfileRow(raw_text=raw_text, claims=claims)
        return self.profile

    def get_current(self) -> FakeResumeProfileRow | None:
        return self.profile


class FakeSession:
    """Task 15: `SendApproved` commits the reservation in its own
    transaction *before* calling the sender, then commits the record step.
    Counting commits lets a test assert that ordering."""

    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


# --- Phase 2 (Task 2/3): job bookkeeping ---------------------------------------


class FakeJobRepo:
    """Mirrors the real `JobRepo` (app.adapters.db.repositories): uniform
    `upsert(**fields)` keyed on `id`, plus the read helpers `RunJob` and the
    stats endpoint use."""

    def __init__(self):
        self.rows: dict[int, object] = {}
        self._next = 1

    def upsert(self, **fields) -> object:
        job_id = fields.pop("id", None) or self._next
        if job_id == self._next:
            self._next += 1
        row = self.rows.get(job_id)
        if row is None:
            row = type("FakeJobRow", (), {"id": job_id})()
            self.rows[job_id] = row
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    def get(self, job_id: int) -> object | None:
        return self.rows.get(job_id)

    def active(self, kind: str) -> object | None:
        return next((r for r in self.rows.values()
                     if r.kind == kind and r.status in ("queued", "running")), None)

    def list_active(self) -> list:
        return [r for r in self.rows.values() if r.status in ("queued", "running")]

    def list_recent(self, limit: int = 50) -> list:
        return sorted(self.rows.values(), key=lambda r: r.id, reverse=True)[:limit]


@dataclass
class FakeRepos:
    """Structural superset of `ResolveRepos` (Task 12), `DraftRepos`
    (Task 14), `SendRepos` (Task 15), `ReviewRepos` (Task 16) and — since
    the Task 16 offline end-to-end test — the `SyncCompanies` company repo
    and `ScrapeRepos` too; see the module docstring. `job` (Phase 2 Task 3)
    is the FastAPI adapter's addition, for the `/api/stats` router."""
    founder: FakeFounderRepo
    company: FakeCompanyRepo
    launch: FakeLaunchRepo
    outreach: FakeOutreachRepo
    email_candidate: FakeEmailCandidateRepo
    news: FakeNewsRepo
    job_posting: FakeJobPostingRepo
    message: FakeMessageRepo
    resume_profile: FakeResumeProfileRepo
    job: FakeJobRepo
    session: FakeSession


RESUME_CLAIMS = [
    {"kind": "project", "text": "built a scheduler in Python",
     "source_span": "Built a scheduler in Python, cut wait 50%", "span_verified": True},
]



# --- Task 14 constants ---------------------------------------------------------

VAHAN_NEWS = "OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI"
VAHAN_DESCRIPTION = (
    "Vahan matches blue-collar workers to jobs over voice, in a dozen Indian languages, "
    "screening and placing delivery and warehouse staff for large gig platforms."
)




# --- Task 15 constants and helpers --------------------------------------------

APPROVED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
APPROVED_BODY = (
    "You're matching blue-collar workers to jobs over voice, in a dozen languages.\n\n"
    "I'm a final-year CS student. I built a scheduler in Python.\n\n"
    "Would you take an intern this summer? Happy to send code."
)


def _add_founder_with_draft(repos: FakeRepos, id: int, full_name: str, email: str | None,
                            state: OutreachState, approved: bool) -> FakeFounderRow:
    """One founder at their own company, an outreach row in `state` with
    `resolved_email`, and the step-0 outbound draft row — `approved_at`
    stamped only when `approved`. Outreach id == founder id (see the
    module docstring)."""
    repos.company.add(id=id, domain=f"co{id}.example.com", name=f"Company {id}")
    founder = repos.founder.add(id=id, company_id=id, full_name=full_name)
    outreach = repos.outreach.add(id=id, founder_id=founder.id, state=str(state))
    outreach.resolved_email = email
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject=f"Backend intern at Company {id}", body=APPROVED_BODY,
        research_source_type="news", research_source_id=1,
        approved_at=APPROVED_AT if approved else None,
    )
    return founder




# --- Task 16 constants and helpers --------------------------------------------

PENDING_SUBJECT = "Backend intern at Vahan"
# The task-14 tests' GOOD body: validates against `VAHAN_NEWS` + the one
# resume claim above, so an edit that keeps it (or changes one word) passes
# `validate_draft` and an edit that breaks a rule fails it.
PENDING_BODY = (
    "You're matching blue-collar workers to jobs over voice at Vahan. "
    "That routing problem is what I keep thinking about.\n\n"
    "I built a scheduler in Python.\n\n"
    "Would you take an intern?"
)
MANUAL_WRITE_ERROR = "contains a banned phrase: 'i hope this email finds you well'"


def _add_pending_founder(repos: FakeRepos, id: int, full_name: str, email: str, *,
                         body: str = PENDING_BODY, last_error: str | None = None,
                         with_row: bool = True) -> FakeFounderRow:
    """One founder at a Vahan-shaped company (name "Vahan" so `PENDING_BODY`
    names it; its one news row's id == the company id), `PENDING_APPROVAL`
    with a resolved address and, unless `with_row=False`, the step-0
    outbound draft row citing that news item. Outreach id == founder id ==
    company id == news id (see the module docstring)."""
    repos.company.add(id=id, domain=f"vahan{id}.ai", name="Vahan",
                      one_liner="Recruitment over voice", long_description=VAHAN_DESCRIPTION)
    founder = repos.founder.add(id=id, company_id=id, full_name=full_name, title="Founder")
    outreach = repos.outreach.add(id=id, founder_id=founder.id,
                                  state=str(OutreachState.PENDING_APPROVAL))
    outreach.resolved_email = email
    outreach.last_error = last_error
    repos.news.add(id=id, company_id=id, title=VAHAN_NEWS, source="The Economic Times")
    if with_row:
        repos.message.upsert(
            outreach_id=outreach.id, sequence_step=0, direction="outbound",
            subject=PENDING_SUBJECT, body=body,
            research_source_type="news", research_source_id=id,
        )
    return founder


