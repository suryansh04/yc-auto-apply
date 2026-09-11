"""Fixtures for Task 12's `ResolveEmails` tests and Task 14's
`DraftOutreach` tests (and any other application-layer test that needs
in-memory repos).

These fakes are deliberately separate from `tests/fakes.py`: that module
holds fakes for the *port* Protocols in `app.ports` (`EmailVerifier`,
`Drafter`, ...), each satisfying a fixed interface pinned by
`test_fakes_satisfy_ports.py`. The repos bundled into `ResolveRepos` /
`DraftRepos` are not ports — they are each application module's own
dependency shape, the same way `ScrapeRepos` got its own local fakes inline
in `test_scrape_company_page.py`. `founder`/`outreach` are wired to share
state (a fake join, mirroring the real `FounderRepo.list_by_state`'s SQL
join to `Outreach.state`) since one `Outreach` row exists per founder.

Task 14 extension: the `repos` fixture returns a single `FakeRepos` bundle
that is a structural superset of both `ResolveRepos` and `DraftRepos` —
each use case only ever touches the attributes it declares, so one bundle
serves both test modules without either having to know about the other's
repos. `ready_founder` / `founder_with_senior_posting` are the Task 14
fixtures; the `repos` fixture also seeds a resume profile with one claim
(the one the task-14 brief's `GOOD` draft cites), which the Task 12 tests
simply ignore.

Deliberate fixture choice: each fake `Outreach` row's `id` is set equal to
its `founder_id` (both come from the same fixture-local counter). Nothing
in `ResolveEmails` or `DraftOutreach` relies on this — they only ever look
outreach rows up via `get_by_founder_id`/`upsert(founder_id=...)` — but the
briefs' own test bodies call `repos.outreach.get(a_founder.id)`, i.e.
address the outreach row by the *founder's* id. Matching the two id spaces
in these fixtures is what makes that call resolve to the right row.
"""

from dataclasses import dataclass

import pytest

from app.domain.states import OutreachState


@dataclass
class FakeCompanyRow:
    id: int
    domain: str | None = None
    name: str = ""
    one_liner: str | None = None
    long_description: str | None = None


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


class FakeNewsRepo:
    def __init__(self):
        self.rows: list[FakeNewsRow] = []

    def add(self, id: int, company_id: int, title: str, source: str | None = None) -> FakeNewsRow:
        row = FakeNewsRow(id=id, company_id=company_id, title=title, source=source)
        self.rows.append(row)
        return row

    def list_by_company_id(self, company_id: int) -> list[FakeNewsRow]:
        return [r for r in self.rows if r.company_id == company_id]


@dataclass
class FakeJobPostingRow:
    id: int
    company_id: int
    title: str
    employment_type: str | None = None
    min_experience: str | None = None


class FakeJobPostingRepo:
    def __init__(self):
        self.rows: list[FakeJobPostingRow] = []

    def add(self, id: int, company_id: int, title: str, employment_type: str | None = None,
            min_experience: str | None = None) -> FakeJobPostingRow:
        row = FakeJobPostingRow(id=id, company_id=company_id, title=title,
                                employment_type=employment_type, min_experience=min_experience)
        self.rows.append(row)
        return row

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

    def get_outbound(self, outreach_id: int, sequence_step: int) -> FakeMessageRow | None:
        return next((r for r in self.rows
                     if r.outreach_id == outreach_id and r.sequence_step == sequence_step
                     and r.direction == "outbound"), None)

    def list_by_outreach_id(self, outreach_id: int) -> list[FakeMessageRow]:
        return [r for r in self.rows if r.outreach_id == outreach_id]


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


@dataclass
class FakeRepos:
    """Structural superset of `ResolveRepos` (Task 12) and `DraftRepos`
    (Task 14) — see the module docstring."""
    founder: FakeFounderRepo
    company: FakeCompanyRepo
    launch: FakeLaunchRepo
    outreach: FakeOutreachRepo
    email_candidate: FakeEmailCandidateRepo
    news: FakeNewsRepo
    job_posting: FakeJobPostingRepo
    message: FakeMessageRepo
    resume_profile: FakeResumeProfileRepo


RESUME_CLAIMS = [
    {"kind": "project", "text": "built a scheduler in Python",
     "source_span": "Built a scheduler in Python, cut wait 50%", "span_verified": True},
]


@pytest.fixture
def repos() -> FakeRepos:
    outreach = FakeOutreachRepo()
    bundle = FakeRepos(
        founder=FakeFounderRepo(outreach),
        company=FakeCompanyRepo(),
        launch=FakeLaunchRepo(),
        outreach=outreach,
        email_candidate=FakeEmailCandidateRepo(),
        news=FakeNewsRepo(),
        job_posting=FakeJobPostingRepo(),
        message=FakeMessageRepo(),
        resume_profile=FakeResumeProfileRepo(),
    )
    bundle.resume_profile.save("Built a scheduler in Python, cut wait 50%", list(RESUME_CLAIMS))
    return bundle


# --- Task 12 fixtures ---------------------------------------------------------


@pytest.fixture
def a_founder(repos: FakeRepos) -> FakeFounderRow:
    """One founder, one-founder company, `NEEDS_EMAIL`. Named/domained to
    match the brief's own test bodies: `generate_candidates("Pratish
    Gandhi", "onefin.com")` yields `pratish@onefin.com` before
    `pratish.gandhi@onefin.com`."""
    repos.company.add(id=1, domain="onefin.com")
    founder = repos.founder.add(
        id=1, company_id=1, full_name="Pratish Gandhi",
        linkedin_url="https://linkedin.com/in/pratish-gandhi",
    )
    repos.outreach.add(id=1, founder_id=founder.id, state=str(OutreachState.NEEDS_EMAIL))
    return founder


@pytest.fixture
def founder_with_launch_email(repos: FakeRepos) -> FakeFounderRow:
    """A single-founder company whose launch post carries a
    `published_email` — the D18 short-circuit applies unconditionally here
    since there is exactly one founder to attribute it to."""
    repos.company.add(id=2, domain="acme.com")
    founder = repos.founder.add(id=2, company_id=2, full_name="Jane Founder")
    repos.outreach.add(id=2, founder_id=founder.id, state=str(OutreachState.NEEDS_EMAIL))
    repos.launch.add(company_id=2, body="Reach me at jane@acme.com", published_email="jane@acme.com")
    return founder


@pytest.fixture
def two_founders(repos: FakeRepos) -> list[FakeFounderRow]:
    """Two unrelated single-founder companies, both `NEEDS_EMAIL`, no
    launches — used to exercise a quota that runs out partway through a
    batch run."""
    repos.company.add(id=3, domain="one.com")
    f1 = repos.founder.add(id=3, company_id=3, full_name="Asha Rao")
    repos.outreach.add(id=3, founder_id=f1.id, state=str(OutreachState.NEEDS_EMAIL))

    repos.company.add(id=4, domain="two.com")
    f2 = repos.founder.add(id=4, company_id=4, full_name="Vikram Shah")
    repos.outreach.add(id=4, founder_id=f2.id, state=str(OutreachState.NEEDS_EMAIL))

    return [f1, f2]


# --- Task 14 fixtures ---------------------------------------------------------

VAHAN_NEWS = "OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI"
VAHAN_DESCRIPTION = (
    "Vahan matches blue-collar workers to jobs over voice, in a dozen Indian languages, "
    "screening and placing delivery and warehouse staff for large gig platforms."
)


@pytest.fixture
def ready_founder(repos: FakeRepos) -> FakeFounderRow:
    """Madhav at Vahan, `READY_TO_DRAFT`, with one news item (id=1 — the id
    the task-14 brief's `GOOD` draft cites) and a long description over the
    80-character floor. No launch, no postings, so the default source list
    is exactly `[news, long_description]`."""
    repos.company.add(id=5, domain="vahan.ai", name="Vahan",
                      one_liner="Recruitment over voice", long_description=VAHAN_DESCRIPTION)
    founder = repos.founder.add(id=5, company_id=5, full_name="Madhav Krishna", title="Founder")
    repos.outreach.add(id=5, founder_id=founder.id, state=str(OutreachState.READY_TO_DRAFT))
    repos.news.add(id=1, company_id=5, title=VAHAN_NEWS, source="The Economic Times")
    return founder


@pytest.fixture
def founder_with_senior_posting(repos: FakeRepos) -> FakeFounderRow:
    """A `READY_TO_DRAFT` founder whose company has no news and no launch,
    only a senior (6+ years) posting and a long description — the D23 case:
    the posting must never be offered, not even as the only alternative to
    the description."""
    repos.company.add(
        id=6, domain="acme.com", name="Acme", one_liner="Payments for fleets",
        long_description=("Acme builds a payments rail for trucking fleets, settling fuel and toll "
                          "spend per vehicle with a ledger reconciled nightly against bank feeds."),
    )
    founder = repos.founder.add(id=6, company_id=6, full_name="Riya Nair")
    repos.outreach.add(id=6, founder_id=founder.id, state=str(OutreachState.READY_TO_DRAFT))
    repos.job_posting.add(id=1, company_id=6, title="Senior Engineer",
                          employment_type="Full-time", min_experience="6+ years")
    return founder
