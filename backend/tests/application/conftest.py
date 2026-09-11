"""Fixtures for Task 12's `ResolveEmails` tests (and, going forward, any
other application-layer test that needs an in-memory `ResolveRepos`).

These fakes are deliberately separate from `tests/fakes.py`: that module
holds fakes for the *port* Protocols in `app.ports` (`EmailVerifier`,
`Drafter`, ...), each satisfying a fixed interface pinned by
`test_fakes_satisfy_ports.py`. The repos bundled into `ResolveRepos` are not
ports — they are this application module's own dependency shape, the same
way `ScrapeRepos` got its own local fakes inline in
`test_scrape_company_page.py`. `founder`/`outreach` are wired to share
state (a fake join, mirroring the real `FounderRepo.list_by_state`'s SQL
join to `Outreach.state`) since one `Outreach` row exists per founder.

Deliberate fixture choice: each fake `Outreach` row's `id` is set equal to
its `founder_id` (both come from the same fixture-local counter). Nothing
in `ResolveEmails` itself relies on this — it only ever looks up outreach
rows via `get_by_founder_id`/`upsert(founder_id=...)` — but the brief's own
test bodies call `repos.outreach.get(a_founder.id)`, i.e. address the
outreach row by the *founder's* id. Matching the two id spaces in these
fixtures is what makes that call resolve to the right row.
"""

from dataclasses import dataclass, field

import pytest

from app.application.resolve_emails import ResolveRepos
from app.domain.states import OutreachState


@dataclass
class FakeCompanyRow:
    id: int
    domain: str | None = None


class FakeCompanyRepo:
    def __init__(self):
        self.rows: dict[int, FakeCompanyRow] = {}

    def add(self, id: int, domain: str | None) -> FakeCompanyRow:
        row = FakeCompanyRow(id=id, domain=domain)
        self.rows[id] = row
        return row

    def get(self, id: int) -> FakeCompanyRow | None:
        return self.rows.get(id)


@dataclass
class FakeLaunchRow:
    company_id: int
    body: str = ""
    published_email: str | None = None


class FakeLaunchRepo:
    def __init__(self):
        self.rows: list[FakeLaunchRow] = []

    def add(self, company_id: int, body: str = "", published_email: str | None = None) -> FakeLaunchRow:
        row = FakeLaunchRow(company_id=company_id, body=body, published_email=published_email)
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


class FakeFounderRepo:
    """Mirrors the real `FounderRepo.list_by_state`, which joins to
    `Outreach.state` — this fake does the equivalent join against the
    `FakeOutreachRepo` it's constructed with."""

    def __init__(self, outreach_repo: FakeOutreachRepo):
        self._outreach = outreach_repo
        self.rows: dict[int, FakeFounderRow] = {}

    def add(self, id: int, company_id: int, full_name: str, linkedin_url: str | None = None) -> FakeFounderRow:
        row = FakeFounderRow(id=id, company_id=company_id, full_name=full_name, linkedin_url=linkedin_url)
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


@pytest.fixture
def repos() -> ResolveRepos:
    outreach = FakeOutreachRepo()
    return ResolveRepos(
        founder=FakeFounderRepo(outreach),
        company=FakeCompanyRepo(),
        launch=FakeLaunchRepo(),
        outreach=outreach,
        email_candidate=FakeEmailCandidateRepo(),
    )


@pytest.fixture
def a_founder(repos: ResolveRepos) -> FakeFounderRow:
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
def founder_with_launch_email(repos: ResolveRepos) -> FakeFounderRow:
    """A single-founder company whose launch post carries a
    `published_email` — the D18 short-circuit applies unconditionally here
    since there is exactly one founder to attribute it to."""
    repos.company.add(id=2, domain="acme.com")
    founder = repos.founder.add(id=2, company_id=2, full_name="Jane Founder")
    repos.outreach.add(id=2, founder_id=founder.id, state=str(OutreachState.NEEDS_EMAIL))
    repos.launch.add(company_id=2, body="Reach me at jane@acme.com", published_email="jane@acme.com")
    return founder


@pytest.fixture
def two_founders(repos: ResolveRepos) -> list[FakeFounderRow]:
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
