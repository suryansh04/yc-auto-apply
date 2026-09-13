"""Root fixtures (final fix wave, F9 / ruling FR-5).

EVERY fixture built on the in-memory `FakeRepos` bundle lives here, built
from the plain fake classes in `tests/fakes_repos.py`; there is no
`tests/application/conftest.py` any more. Ruling FR-5 asked for the shared
fixtures to move here and the application-only ones to stay in a package
conftest, but pytest 9.1.1 drops a package conftest's fixtures for the
second interleaved argument even when no conftest imports another
(`pytest tests/application/test_resolve_emails.py tests/test_cli.py
tests/application/test_draft_outreach.py` -> `fixture 'ready_founder' not
found`, reproduced with the split in place), so the only layout that makes
an interleaved invocation resolve every fixture is a single root conftest.

The brief's `test_cli_review.py` names `a_pending_message` /
`a_news_source`; they are built from the same fake row classes, so the
review screen is rendered from exactly the shapes the use case hands the
CLI.
"""

import copy
from types import SimpleNamespace

import pytest

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _ignore_operator_dotenv(monkeypatch):
    """The suite must behave identically whether or not the operator has a
    real `backend/.env` (live run, 2026-09-13: LLM_PROVIDER=sarvam in the
    real file flipped the Gemini free-tier CLI tests). Disable the env file
    for every test; tests set what they need via monkeypatch.setenv."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
from datetime import datetime


from app.domain.states import OutreachState
from tests.fakes_repos import (
    MANUAL_WRITE_ERROR,
    VAHAN_DESCRIPTION,
    PENDING_BODY,
    PENDING_SUBJECT,
    RESUME_CLAIMS,
    VAHAN_NEWS,
    FakeCompanyRepo,
    FakeEmailCandidateRepo,
    FakeFounderRepo,
    FakeFounderRow,
    FakeJobPostingRepo,
    FakeJobRepo,
    FakeLaunchRepo,
    FakeMessageRepo,
    FakeMessageRow,
    FakeNewsRepo,
    FakeNewsRow,
    FakeOutreachRepo,
    FakeRepos,
    FakeResumeProfileRepo,
    FakeSession,
    _add_founder_with_draft,
    _add_pending_founder,
)


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
        job=FakeJobRepo(),
        session=FakeSession(),
    )
    # deepcopy (Task 16 fix round 1): a test that flips a claim's `span_verified`
    # in place must not leak into the module-level RESUME_CLAIMS.
    bundle.resume_profile.save("Built a scheduler in Python, cut wait 50%", copy.deepcopy(RESUME_CLAIMS))
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



# --- Task 15 fixtures ---------------------------------------------------------


@pytest.fixture
def one_approved(repos: FakeRepos) -> FakeFounderRow:
    """Madhav, `APPROVED`, draft approved, address resolved."""
    return _add_founder_with_draft(repos, 10, "Madhav Krishna", "madhav@example.com",
                                   OutreachState.APPROVED, approved=True)


@pytest.fixture
def pending_and_approved(repos: FakeRepos) -> tuple[FakeFounderRow, FakeFounderRow]:
    """One `PENDING_APPROVAL` founder (draft not approved) and one
    `APPROVED` founder at `approved@example.com` — the brief's own address."""
    pending = _add_founder_with_draft(repos, 11, "Riya Nair", "pending@example.com",
                                      OutreachState.PENDING_APPROVAL, approved=False)
    approved = _add_founder_with_draft(repos, 12, "Asha Rao", "approved@example.com",
                                       OutreachState.APPROVED, approved=True)
    return pending, approved


@pytest.fixture
def five_approved(repos: FakeRepos) -> list[FakeFounderRow]:
    names = ["Asha Rao", "Vikram Shah", "Riya Nair", "Pratish Gandhi", "Jane Founder"]
    return [
        _add_founder_with_draft(repos, 20 + i, name, f"founder{i}@example.com",
                                OutreachState.APPROVED, approved=True)
        for i, name in enumerate(names)
    ]


@pytest.fixture
def sent_earlier(repos: FakeRepos):
    """`sent_earlier(n, at=...)` adds `n` already-sent outbound rows (with
    Gmail ids) for `SENT` founders, stamped `at` — for the rolling-window
    cap tests (ruling R-A)."""
    def _add(n: int, at: datetime) -> None:
        for i in range(n):
            id = 100 + len(repos.message.rows)
            _add_founder_with_draft(repos, id, f"Sent Earlier{i}", f"earlier{id}@example.com",
                                    OutreachState.SENT, approved=True)
            row = repos.message.get_outbound(id, 0)
            row.sent_at = at
            row.gmail_message_id = f"earlier-{id}"
            row.gmail_thread_id = f"earlier-thread-{id}"
    return _add


# --- Task 16 fixtures ---------------------------------------------------------


@pytest.fixture
def one_pending(repos: FakeRepos) -> FakeFounderRow:
    """Madhav, `PENDING_APPROVAL`, a validating draft, no `last_error`."""
    return _add_pending_founder(repos, 30, "Madhav Krishna", "madhav@example.com")


@pytest.fixture
def two_pending(repos: FakeRepos) -> tuple[FakeFounderRow, FakeFounderRow]:
    return (_add_pending_founder(repos, 31, "Madhav Krishna", "madhav@example.com"),
            _add_pending_founder(repos, 32, "Asha Rao", "asha@example.com"))


@pytest.fixture
def manual_write_pending(repos: FakeRepos) -> FakeFounderRow:
    """Riya: the drafter failed validation twice, so the rejected draft is
    stored and `last_error` carries the reasons (needs manual write)."""
    return _add_pending_founder(
        repos, 33, "Riya Nair", "riya@example.com",
        body="I hope this email finds you well. Would you take an intern?",
        last_error=MANUAL_WRITE_ERROR,
    )


@pytest.fixture
def no_material_pending(repos: FakeRepos) -> FakeFounderRow:
    """Vikram: no research material at all, so `DraftOutreach` never
    called the drafter and wrote NO message row - only the flag."""
    founder = _add_pending_founder(
        repos, 34, "Vikram Shah", "vikram@example.com", with_row=False,
        last_error="needs manual write: no research material",
    )
    repos.news.rows = [n for n in repos.news.rows if n.company_id != 34]
    repos.company.get(34).long_description = "Too short."
    return founder


@pytest.fixture
def a_news_source() -> FakeNewsRow:
    return FakeNewsRow(id=1, company_id=5, title=VAHAN_NEWS, source="The Economic Times")


@pytest.fixture
def a_pending_message() -> FakeMessageRow:
    return FakeMessageRow(
        id=1, outreach_id=5, sequence_step=0, direction="outbound",
        subject=PENDING_SUBJECT, body=PENDING_BODY,
        research_source_type="news", research_source_id=1,
    )


# --- Task 4 (API) fixtures ----------------------------------------------------


def _draft(repos: FakeRepos, id: int, full_name: str, email: str, **kwargs):
    """A `pending_draft`-shaped wrapper for the API tests: `.outreach` and
    `.founder` off one `_add_pending_founder` row. `resolved_email` lives on
    the outreach row in the real schema, but the brief's own tests read it
    off `.founder` (`pending_draft.founder.resolved_email`), so it is
    mirrored onto the founder row here for convenience - nothing in the
    router or the use case relies on it being there."""
    founder = _add_pending_founder(repos, id, full_name, email, **kwargs)
    outreach = repos.outreach.get_by_founder_id(founder.id)
    founder.resolved_email = outreach.resolved_email
    return SimpleNamespace(outreach=outreach, founder=founder)


@pytest.fixture
def pending_draft(repos: FakeRepos):
    """Madhav, `PENDING_APPROVAL`, a validating draft, no `last_error` -
    the queue's single-item case."""
    return _draft(repos, 40, "Madhav Krishna", "madhav@example.com")


@pytest.fixture
def two_pending_drafts(repos: FakeRepos):
    """Two news-backed, team-size-less founders, returned in rank order
    (tie-break is founder name ascending: Asha before Madhav) so a test can
    assert the queue's order equals this fixture's order directly."""
    return (
        _draft(repos, 41, "Asha Rao", "asha@example.com"),
        _draft(repos, 42, "Madhav Krishna", "madhav@example.com"),
    )


@pytest.fixture
def flagged_draft(repos: FakeRepos):
    """Riya: the drafter's rejected draft is stored and `last_error` is set
    (needs manual write) - the 409-on-approve case until it is edited."""
    return _draft(
        repos, 43, "Riya Nair", "riya@example.com",
        body="I hope this email finds you well. Would you take an intern?",
        last_error=MANUAL_WRITE_ERROR,
    )


@pytest.fixture
def approved_draft(repos: FakeRepos):
    """Madhav, `APPROVED` with an approved draft and a resolved address -
    the send API's single-item case. `.outreach`/`.founder`, matching the
    `pending_draft`/`flagged_draft` shape above."""
    founder = _add_founder_with_draft(repos, 60, "Madhav Krishna", "madhav@example.com",
                                      OutreachState.APPROVED, approved=True)
    outreach = repos.outreach.get_by_founder_id(founder.id)
    return SimpleNamespace(outreach=outreach, founder=founder)


@pytest.fixture
def fake_sender(repos: FakeRepos):
    """Task 5: the ONLY way `POST /api/send` is ever reached with
    something other than a real Gmail client - see `app/api/routers/send.py`'s
    module docstring. `Repos.sender` (`app/api/deps.py`) defaults to `None`;
    this fixture sets it directly on the `FakeRepos` instance the `client`
    fixture is built from (a plain object, so this attribute assignment
    needs no change to `FakeRepos` itself)."""
    from tests.fakes import FakeSender

    sender = FakeSender()
    repos.sender = sender
    return sender


@pytest.fixture
def manual_founder(repos: FakeRepos):
    """Pratish at OneFin, `NEEDS_MANUAL_EMAIL`, with one already-judged
    candidate (`catch_all` - the domain's verdict, so no address could be
    guessed) - the manual queue's single-item case. `.outreach`/`.founder`."""
    repos.company.add(id=70, domain="onefin.in", name="OneFin")
    founder = repos.founder.add(id=70, company_id=70, full_name="Pratish Gandhi",
                                linkedin_url="https://linkedin.com/in/pratish-gandhi")
    outreach = repos.outreach.add(id=70, founder_id=founder.id,
                                  state=str(OutreachState.NEEDS_MANUAL_EMAIL))
    repos.email_candidate.upsert(founder_id=founder.id, address="pratish@onefin.in",
                                 priority=1, verdict="catch_all")
    return SimpleNamespace(outreach=outreach, founder=founder)


@pytest.fixture
def good_body() -> str:
    """The task-14 GOOD body: validates against `pending_draft`'s cited
    news item, Vahan's `long_description` and the one resume claim."""
    return PENDING_BODY


# --- Task 6 fixtures: the background job runner -------------------------------
#
# These build fakes for the TWO seams `app/api/jobs.py` exposes -
# `job_session_factory` (opens the thread's own "session") and
# `job_runner_factory` (builds the `RunJob` bookkeeping object for a given
# session) - both stored on `app.state` by `create_app` and read fresh on
# every call, so a fixture can swap them on an already-created `client`
# simply by assigning `client.app.state.job_session_factory` /
# `job_runner_factory` after the fact. The runner side always goes through
# the SAME `repos.job` (`FakeJobRepo`) the `GET /api/jobs` route reads via
# `get_repos`, regardless of which fake "session" object it was called
# with - that is what makes the background thread's writes visible to the
# request thread's reads in these tests, with no real database involved.
#
# Threads are real; only the stage bodies (`JOB_KINDS` entries) and the
# session/runner objects are faked - `monkeypatch.setattr` rebinds
# `app.api.jobs.JOB_KINDS` itself, which `start_job` (defined in that same
# module) looks up by module-global name, so the swap is visible to it.


class _FixedClock:
    def now(self):
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)


class _FakeJobSession:
    """The thread's "own session" in tests: just needs `.close()`. The
    fake stage functions never touch it for real I/O."""

    def close(self) -> None:
        pass


def default_test_job_factories(repos):
    """The `client` fixture's default `job_session_factory` /
    `job_runner_factory` - used by EVERY API test (Task 6 fixtures below
    layer stage-specific fakes over these on `client.app.state`
    afterward). Without this, `create_app`'s startup hook
    (`recover_stale_jobs`) would fall back to the production defaults,
    which call `get_settings().database_url` - a required field with no
    default - and blow up every API test under the offline suite's `env
    -i` (global constraint: no env vars, no DB, no network)."""
    from app.application.run_job import RunJob

    def session_factory():
        return _FakeJobSession()

    def runner_factory(session):
        return RunJob(repos.job, _FixedClock(), commit=lambda: None)

    return session_factory, runner_factory


def _signalling_runner_factory(repos, event):
    """A `RunJob` bound to the shared `FakeJobRepo`, whose `finish`/`fail`
    also set `event` - AFTER the row is actually updated, so a test that
    does `event.wait()` then reads `GET /api/jobs` never races the
    background thread's own write."""
    from app.application.run_job import RunJob

    class _SignallingRunJob(RunJob):
        def finish(self, job_id, summary: str = "") -> None:
            super().finish(job_id, summary)
            event.set()

        def fail(self, job_id, exc: BaseException) -> None:
            super().fail(job_id, exc)
            event.set()

    def runner_factory(session):
        return _SignallingRunJob(repos.job, _FixedClock(), commit=lambda: None)

    return runner_factory


@pytest.fixture
def job_probe(client, repos, monkeypatch):
    """Every `JOB_KINDS` stage becomes a fake that records which kind ran
    and how many sessions it saw - `sessions_opened` is incremented by the
    fake stage itself, i.e. once per job actually executed in the
    background thread (never by the request thread, which never runs a
    stage at all)."""
    import threading

    from app.api import jobs as jobs_impl

    class Probe:
        def __init__(self):
            self.ran: list[str] = []
            self.sessions_opened = 0
            self.finished = threading.Event()

    probe = Probe()

    def make_stage(kind):
        def _stage(session, progress):
            probe.sessions_opened += 1
            probe.ran.append(kind)
            progress(1, 1)
            return f"{kind} ran"
        return _stage

    monkeypatch.setattr(jobs_impl, "JOB_KINDS", {k: make_stage(k) for k in jobs_impl.JOB_KINDS})

    client.app.state.job_session_factory = _FakeJobSession
    client.app.state.job_runner_factory = _signalling_runner_factory(repos, probe.finished)
    return probe


@pytest.fixture
def blocking_job(client, repos, monkeypatch):
    """A stage that blocks until `.release()` is called and reports
    progress on demand via `.report(done, total)` - used to hold a job in
    `running` long enough to assert on it (the second-start-is-409 and
    progress-while-running tests)."""
    import threading

    from app.api import jobs as jobs_impl

    class Blocker:
        def __init__(self):
            self._release_event = threading.Event()
            self._progress_ready = threading.Event()
            self._progress_fn = None

        def release(self) -> None:
            self._release_event.set()

        def report(self, done: int, total: int | None = None) -> None:
            assert self._progress_ready.wait(timeout=5), "stage never started"
            self._progress_fn(done, total)

    blocker = Blocker()

    def make_stage(kind):
        def _stage(session, progress):
            blocker._progress_fn = progress
            blocker._progress_ready.set()
            assert blocker._release_event.wait(timeout=5), "blocking_job was never released"
            return f"{kind} ran"
        return _stage

    monkeypatch.setattr(jobs_impl, "JOB_KINDS", {k: make_stage(k) for k in jobs_impl.JOB_KINDS})

    client.app.state.job_session_factory = _FakeJobSession
    client.app.state.job_runner_factory = _signalling_runner_factory(repos, threading.Event())
    yield blocker
    # Safety net: an assertion failure above must not leave the background
    # thread parked forever and hang the test run.
    blocker.release()


@pytest.fixture
def failing_job(client, repos, monkeypatch):
    """Every `JOB_KINDS` stage raises immediately - proves a failure
    records only `type(exc).__name__` (never the message, which can carry
    a secret) and frees the kind for a retry."""
    import threading

    from app.api import jobs as jobs_impl

    class Probe:
        def __init__(self):
            self.finished = threading.Event()

    probe = Probe()

    def make_stage(kind):
        def _stage(session, progress):
            raise RuntimeError("simulated failure containing a fake api_key=secret-token")
        return _stage

    monkeypatch.setattr(jobs_impl, "JOB_KINDS", {k: make_stage(k) for k in jobs_impl.JOB_KINDS})

    client.app.state.job_session_factory = _FakeJobSession
    client.app.state.job_runner_factory = _signalling_runner_factory(repos, probe.finished)
    return probe


@pytest.fixture
def stale_job_client(repos):
    """A `scrape` job left `running` by a "crash" two hours ago, then a
    fresh app booted against the SAME `repos` - `create_app`'s startup
    hook (`recover_stale_jobs`) must mark it `failed`/`interrupted` before
    the app starts serving, using the job factories passed in here (never
    a real database)."""
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient

    from app.api.main import create_app

    repos.job.upsert(
        kind="scrape", status="running", created_by="ui",
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        done=0, total=None, summary=None, error=None, finished_at=None,
    )

    def runner_factory(session):
        from app.application.run_job import RunJob

        return RunJob(repos.job, _FixedClock(), commit=lambda: None)

    app = create_app(
        repos_factory=lambda: repos,
        job_session_factory=_FakeJobSession,
        job_runner_factory=runner_factory,
    )
    with TestClient(app) as c:
        yield c
