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
from datetime import datetime, timezone


from app.domain.states import OutreachState
from tests.fakes_repos import (
    MANUAL_WRITE_ERROR,
    VAHAN_DESCRIPTION,
    PENDING_BODY,
    PENDING_SUBJECT,
    RESUME_CLAIMS,
    VAHAN_NEWS,
    FakeCompanyRepo,
    FakeCredentialRepo,
    FakeEmailCandidateRepo,
    FakeFounderRepo,
    FakeFounderRow,
    FakeGmailReader,
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
    FakeSettingsRepo,
    _add_founder_with_draft,
    _add_pending_founder,
)


@pytest.fixture
def repos() -> FakeRepos:
    outreach = FakeOutreachRepo()
    message = FakeMessageRepo()
    outreach._message_repo = message  # Task 8: lets FakeOutreachRepo.get_by_thread_id look messages up
    bundle = FakeRepos(
        founder=FakeFounderRepo(outreach),
        company=FakeCompanyRepo(),
        launch=FakeLaunchRepo(),
        outreach=outreach,
        email_candidate=FakeEmailCandidateRepo(),
        news=FakeNewsRepo(),
        job_posting=FakeJobPostingRepo(),
        message=message,
        resume_profile=FakeResumeProfileRepo(),
        job=FakeJobRepo(),
        session=FakeSession(),
        credential=FakeCredentialRepo(),
        settings=FakeSettingsRepo(),
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


# --- Task 8 fixtures: PollReplies ----------------------------------------------


@pytest.fixture
def sent_outreach(repos: FakeRepos):
    """One founder, `SENT`, with the step-0 outbound draft row carrying a
    Gmail thread id — the base case `PollReplies` matches inbound mail
    against. Also seeds a `GmailCredential` row for "me@gmail.com" as
    `gmail-auth` would already have run (Task 8 handover note 1:
    `set_history_id` needs an existing row — NOT NULL on `refresh_token`),
    with a starting history cursor so a normal poll takes the
    `history_since` path rather than the first-ever-poll fallback.
    `.outreach` / `.founder` / `.thread_id`."""
    from types import SimpleNamespace

    repos.company.add(id=200, domain="onefin.in", name="OneFin")
    founder = repos.founder.add(id=200, company_id=200, full_name="Pratish Gandhi")
    outreach = repos.outreach.add(id=200, founder_id=founder.id, state=str(OutreachState.SENT))
    outreach.resolved_email = "pratish@onefin.in"
    outreach.sequence_step = 0
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject="Backend intern at OneFin", body="You're building payments infra...",
        gmail_message_id="sent-200", gmail_thread_id="thread-200",
        approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    repos.credential.upsert(
        account_email="me@gmail.com", refresh_token="refresh-token-abc123", last_history_id="100",
    )
    return SimpleNamespace(outreach=outreach, founder=founder, thread_id="thread-200")


@pytest.fixture
def fake_reader() -> FakeGmailReader:
    return FakeGmailReader(account="me@gmail.com")


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
    """The thread's "own session" in tests: needs `.close()` and (fix
    round 1) `.rollback()`, since `start_job`'s `IntegrityError` handling
    calls `sync_session.rollback()` even on the ordinary (non-racing)
    path's session type. The fake stage functions never touch it for real
    I/O."""

    def close(self) -> None:
        pass

    def rollback(self) -> None:
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


class _TrackedSession:
    """A `_FakeJobSession` that reports its own identity and close() calls
    to a shared recorder - fix round 1 (M1/M8/M12): proving "the thread
    never uses the request's session" and "every session opened is closed
    on every path" needs sessions a test can actually tell apart, not just
    a count of how many times a stage ran."""

    def __init__(self, recorder: "_SessionRecorder"):
        self._recorder = recorder
        self.id = recorder.next_id()
        recorder.opened.append(self.id)

    def close(self) -> None:
        self._recorder.closed.append(self.id)

    def rollback(self) -> None:
        pass


class _SessionRecorder:
    def __init__(self):
        self.opened: list[int] = []
        self.closed: list[int] = []
        self._counter = 0

    def next_id(self) -> int:
        self._counter += 1
        return self._counter

    def session_factory(self):
        return _TrackedSession(self)

    def all_closed(self) -> bool:
        return sorted(self.opened) == sorted(self.closed)


@pytest.fixture
def job_probe(client, repos, monkeypatch):
    """Every `JOB_KINDS` stage becomes a fake that records which kind ran
    and which SESSION OBJECT it was actually given. `session_factory` is a
    `_SessionRecorder` that hands out a fresh, distinguishable
    `_TrackedSession` on every call and records opens/closes, so
    `job_probe` can assert real session IDENTITY (fix round 1, M1) - not
    just an invocation count, which a mutant that shares the request's
    session with the thread would still satisfy."""
    import threading

    from app.api import jobs as jobs_impl

    class Probe:
        def __init__(self):
            self.ran: list[str] = []
            self.sessions_opened = 0
            self.stage_session_ids: list[int] = []
            self.finished = threading.Event()
            self.recorder = _SessionRecorder()

    probe = Probe()

    def make_stage(kind):
        def _stage(session, progress):
            probe.sessions_opened += 1
            probe.ran.append(kind)
            probe.stage_session_ids.append(session.id)
            progress(1, 1)
            return f"{kind} ran"
        return _stage

    monkeypatch.setattr(jobs_impl, "JOB_KINDS", {k: make_stage(k) for k in jobs_impl.JOB_KINDS})

    client.app.state.job_session_factory = probe.recorder.session_factory
    client.app.state.job_runner_factory = _signalling_runner_factory(repos, probe.finished)
    return probe


@pytest.fixture
def blocking_job(client, repos, monkeypatch):
    """A stage that blocks until `.release()` is called and reports
    progress on demand via `.report(done, total)` - used to hold a job in
    `running` long enough to assert on it (the second-start-is-409 and
    progress-while-running tests). Sessions are `_TrackedSession`s too, so
    the same fixture also proves close-on-every-path for the 409 path
    (fix round 1, M8/M12): the loser of a double-click never runs a stage
    at all, but its synchronous bookkeeping session must still be closed."""
    import threading

    from app.api import jobs as jobs_impl

    class Blocker:
        def __init__(self):
            self._release_event = threading.Event()
            self._progress_ready = threading.Event()
            self._progress_fn = None
            self.recorder = _SessionRecorder()

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

    client.app.state.job_session_factory = blocker.recorder.session_factory
    client.app.state.job_runner_factory = _signalling_runner_factory(repos, threading.Event())
    yield blocker
    # Safety net: an assertion failure above must not leave the background
    # thread parked forever and hang the test run.
    blocker.release()


@pytest.fixture
def failing_job(client, repos, monkeypatch):
    """Every `JOB_KINDS` stage raises immediately - proves a failure
    records only `type(exc).__name__` (never the message, which can carry
    a secret) and frees the kind for a retry. `_TrackedSession`s again, so
    this fixture also proves the thread's session is closed on the
    exception path (M8/M12)."""
    import threading

    from app.api import jobs as jobs_impl

    class Probe:
        def __init__(self):
            self.finished = threading.Event()
            self.recorder = _SessionRecorder()

    probe = Probe()

    def make_stage(kind):
        def _stage(session, progress):
            raise RuntimeError("simulated failure containing a fake api_key=secret-token")
        return _stage

    monkeypatch.setattr(jobs_impl, "JOB_KINDS", {k: make_stage(k) for k in jobs_impl.JOB_KINDS})

    client.app.state.job_session_factory = probe.recorder.session_factory
    client.app.state.job_runner_factory = _signalling_runner_factory(repos, probe.finished)
    return probe


@pytest.fixture
def poisoning_job(client, repos, monkeypatch):
    """Fix round 1 (T6-2, the serious one): a stage that flushes a bad row
    then raises - simulating SQLAlchemy's real "rollback-required" state
    after a failed flush, where the NEXT flush on that same session
    (exactly what `JobRepo.upsert` - and so `fail()` - does) raises
    `PendingRollbackError` unless something rolls back first.

    `_PoisonableSession.flush()` reproduces that in two parts: the FIRST
    call (the stage's own "bad row" write) poisons the session; every
    call after that raises, until `rollback()` clears it.
    `_SessionBoundJobRepo` mirrors the real `app.adapters.db.repositories.
    JobRepo.upsert`, which calls `session.flush()` before its write takes
    effect — so if `fail()` runs on a still-poisoned session, the
    in-memory row is never actually updated to `failed`, the same as a
    real DB row whose UPDATE never durably lands. Without
    `own_session.rollback()` ahead of `fail()` (the fix), that flush call
    raises, escapes `own_runner.fail(...)`, and — pre-fix — would have
    escaped `_run` entirely, carrying this stage's secret-bearing message.
    """
    import threading

    from app.api import jobs as jobs_impl
    from app.application.run_job import RunJob

    class Probe:
        def __init__(self):
            self.finished = threading.Event()

    probe = Probe()

    class _PoisonableSession:
        def __init__(self):
            self.poisoned = False

        def flush(self) -> None:
            # A NORMAL flush: raises only while already poisoned (this is
            # what `_SessionBoundJobRepo.upsert` calls during `fail()`'s
            # write - it must succeed cleanly once `rollback()` has run,
            # not poison the session all over again).
            if self.poisoned:
                raise RuntimeError(
                    "This Session's transaction has been rolled back due to a "
                    "previous exception during flush (simulated PendingRollbackError)"
                )

        def simulate_bad_row(self) -> None:
            # What the STAGE calls: a real bad-row flush both raises
            # AND leaves the session poisoned. Kept separate from the
            # generic `flush()` above so `fail()`'s own later, legitimate
            # flush (once rolled back) does not re-poison itself.
            self.poisoned = True

        def commit(self) -> None:
            if self.poisoned:
                raise RuntimeError(
                    "This Session's transaction has been rolled back due to a "
                    "previous exception during flush (simulated PendingRollbackError)"
                )

        def rollback(self) -> None:
            self.poisoned = False

        def close(self) -> None:
            pass

    class _SessionBoundJobRepo:
        """Delegates every read/write to the shared `repos.job`
        (`FakeJobRepo`), but gates `upsert` on `session.flush()` first -
        exactly where the real `JobRepo.upsert` flushes, and exactly
        where a still-poisoned session raises."""

        def __init__(self, session, real_repo):
            self._session = session
            self._real = real_repo

        def upsert(self, **fields):
            self._session.flush()
            return self._real.upsert(**fields)

        def get(self, job_id):
            return self._real.get(job_id)

        def active(self, kind):
            return self._real.active(kind)

        def list_active(self):
            return self._real.list_active()

    def make_stage(kind):
        def _stage(session, progress):
            session.simulate_bad_row()  # a bad row's flush fails -> poisons the session
            raise RuntimeError("boom while drafting api_key=sk-fake-secret-token")
        return _stage

    monkeypatch.setattr(jobs_impl, "JOB_KINDS", {k: make_stage(k) for k in jobs_impl.JOB_KINDS})

    class _SignallingRunJob(RunJob):
        def fail(self, job_id, exc: BaseException) -> None:
            super().fail(job_id, exc)
            probe.finished.set()

    def runner_factory(session):
        return _SignallingRunJob(_SessionBoundJobRepo(session, repos.job), _FixedClock(),
                                 commit=session.commit)

    client.app.state.job_session_factory = _PoisonableSession
    client.app.state.job_runner_factory = runner_factory
    return probe


@pytest.fixture
def racing_job(client):
    """Fix round 1 (ruling T6-4): simulates the check-then-insert race
    `RunJob.start` cannot close by itself - two requests both see
    `active(kind) is None`, both try to insert, and the database's
    partial unique index rejects the loser with `IntegrityError`. This
    fake repo's `active()` reports free on the first call (both "requests"
    pass the guarded check) and reports the winner from then on
    (`start_job`'s post-`IntegrityError` re-read); `upsert()` raises on
    the first attempt to write a `running` row, exactly where a real
    unique-index violation would surface."""
    from sqlalchemy.exc import IntegrityError

    class _Row:
        def __init__(self, id, kind):
            self.id = id
            self.kind = kind
            self.status = "running"

    winner = _Row(id=42, kind="scrape")

    class _RacyJobRepo:
        def __init__(self):
            self.raised = False

        def active(self, kind):
            return winner if self.raised else None

        def upsert(self, **fields):
            if not self.raised and fields.get("status") == "running":
                self.raised = True
                raise IntegrityError("insert into jobs ...", {}, Exception("uq_jobs_kind_active"))
            return winner

        def get(self, job_id):
            return winner if job_id == winner.id else None

        def list_active(self):
            return [winner]

    def runner_factory(session):
        from app.application.run_job import RunJob

        return RunJob(_RacyJobRepo(), _FixedClock(), commit=lambda: None)

    client.app.state.job_session_factory = _FakeJobSession
    client.app.state.job_runner_factory = runner_factory
    return winner


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


@pytest.fixture
def unlabelled_reply(repos: FakeRepos):
    """One `SENT` outreach with the step-0 outbound draft (the context
    `ClassifyReply` must hand the classifier alongside the reply itself —
    "Sure, Friday works" is unclassifiable on its own) plus one inbound
    reply row with `label=None` — the base case `ClassifyReply` classifies.
    `next_action_at` is stamped with a future timestamp (a pending
    follow-up) so a test can assert a human label actually clears it, not
    just that it stays `None`. `.outreach` / `.founder` / `.message` (the
    reply row)."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    repos.company.add(id=300, domain="foo.io", name="Foo")
    founder = repos.founder.add(id=300, company_id=300, full_name="Asha Founder")
    outreach = repos.outreach.add(id=300, founder_id=founder.id, state=str(OutreachState.SENT))
    outreach.resolved_email = "asha@foo.io"
    outreach.sequence_step = 0
    outreach.next_action_at = datetime(2026, 9, 20, tzinfo=timezone.utc)
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject="Backend intern at Foo", body="You're building payments tooling. Would you take an intern?",
        gmail_message_id="sent-300", gmail_thread_id="thread-300",
        approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    reply = repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="inbound",
        subject="Re: Backend intern at Foo", body="Sure, tell me more.",
        gmail_message_id="reply-300", gmail_thread_id="thread-300",
        received_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    return SimpleNamespace(outreach=outreach, founder=founder, message=reply)


@pytest.fixture
def labelled_reply(repos: FakeRepos):
    """Same shape as `unlabelled_reply`, but already classified — models an
    operator's manual correction (Task 10) that a re-run must never
    overwrite. The outreach is already `REPLIED`, matching what a real
    human label would already have done."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    repos.company.add(id=301, domain="bar.io", name="Bar")
    founder = repos.founder.add(id=301, company_id=301, full_name="Vikram Founder")
    outreach = repos.outreach.add(id=301, founder_id=founder.id, state=str(OutreachState.REPLIED))
    outreach.resolved_email = "vikram@bar.io"
    outreach.sequence_step = 0
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject="Backend intern at Bar", body="You're building fleet routing. Would you take an intern?",
        gmail_message_id="sent-301", gmail_thread_id="thread-301",
        approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    reply = repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="inbound",
        subject="Re: Backend intern at Bar", body="Not hiring interns right now.",
        gmail_message_id="reply-301", gmail_thread_id="thread-301",
        received_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        label="REJECTED", confidence=0.9, rationale="operator-corrected",
        classified_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    return SimpleNamespace(outreach=outreach, founder=founder, message=reply)


@pytest.fixture
def two_unlabelled_replies(repos: FakeRepos):
    """Two independent `SENT` outreaches, each with an unlabelled inbound
    reply — used for the "one failure doesn't stop the batch" and "limit"
    tests, where a single reply isn't enough to prove ordering/partial
    processing. Returns the two reply (message) rows, in the order
    `ClassifyReply` will see them."""
    from datetime import datetime, timezone

    replies = []
    for cid, name in [(310, "Baz"), (311, "Qux")]:
        repos.company.add(id=cid, domain=f"{name.lower()}.io", name=name)
        founder = repos.founder.add(id=cid, company_id=cid, full_name=f"Founder {name}")
        outreach = repos.outreach.add(id=cid, founder_id=founder.id, state=str(OutreachState.SENT))
        outreach.resolved_email = f"founder@{name.lower()}.io"
        outreach.sequence_step = 0
        repos.message.upsert(
            outreach_id=outreach.id, sequence_step=0, direction="outbound",
            subject=f"Backend intern at {name}", body=f"You're building things at {name}. Intern?",
            gmail_message_id=f"sent-{cid}", gmail_thread_id=f"thread-{cid}",
            approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        reply = repos.message.upsert(
            outreach_id=outreach.id, sequence_step=0, direction="inbound",
            subject=f"Re: Backend intern at {name}", body=f"reply body for {name}",
            gmail_message_id=f"reply-{cid}", gmail_thread_id=f"thread-{cid}",
            received_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        replies.append(reply)
    return tuple(replies)


# --- Task 10 fixtures: thread view / label correction -------------------------


@pytest.fixture
def thread_with_reply(repos: FakeRepos):
    """One `SENT` outreach with a step-0 outbound send and one unlabelled
    inbound reply, both carrying the same Gmail thread id — the base case
    `GET /api/outreach/{id}/thread` renders and `POST
    /api/messages/{id}/label` corrects. `.outreach` / `.founder` /
    `.reply` (the inbound message row) / `.thread_id`."""
    from types import SimpleNamespace

    repos.company.add(id=400, domain="qux.io", name="Qux")
    founder = repos.founder.add(id=400, company_id=400, full_name="Nisha Founder")
    outreach = repos.outreach.add(id=400, founder_id=founder.id, state=str(OutreachState.SENT))
    outreach.resolved_email = "nisha@qux.io"
    outreach.sequence_step = 0
    outreach.next_action_at = datetime(2026, 9, 20, tzinfo=timezone.utc)
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject="Backend intern at Qux", body="You're building things at Qux. Intern?",
        gmail_message_id="sent-400", gmail_thread_id="thread-400",
        approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    reply = repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="inbound",
        subject="Re: Backend intern at Qux", body="Tell me more, please.",
        gmail_message_id="reply-400", gmail_thread_id="thread-400",
        received_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    return SimpleNamespace(outreach=outreach, founder=founder, reply=reply, thread_id="thread-400")


@pytest.fixture
def thread_with_auto_reply(repos: FakeRepos):
    """One `SENT` outreach whose inbound reply was already (mis)classified
    `AUTO_REPLY` — the base case for the "correcting a machine label to a
    human one cancels the sequence" test (brief step 1's
    `test_correcting_to_a_human_label_cancels_follow_ups`): the machine
    label never transitioned the outreach (spec §5's central rule), so
    `next_action_at` is still the pending follow-up a correct human label
    must clear. `.outreach` / `.founder` / `.reply`."""
    from types import SimpleNamespace

    repos.company.add(id=401, domain="quux.io", name="Quux")
    founder = repos.founder.add(id=401, company_id=401, full_name="Omar Founder")
    outreach = repos.outreach.add(id=401, founder_id=founder.id, state=str(OutreachState.SENT))
    outreach.resolved_email = "omar@quux.io"
    outreach.sequence_step = 0
    outreach.next_action_at = datetime(2026, 9, 20, tzinfo=timezone.utc)
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject="Backend intern at Quux", body="You're building things at Quux. Intern?",
        gmail_message_id="sent-401", gmail_thread_id="thread-401",
        approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    reply = repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="inbound",
        subject="Re: Backend intern at Quux", body="I'm out of office until next week.",
        gmail_message_id="reply-401", gmail_thread_id="thread-401",
        received_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        label="AUTO_REPLY", confidence=0.8, rationale="autoresponder",
        classified_at=datetime(2026, 9, 2, 1, tzinfo=timezone.utc),
    )
    return SimpleNamespace(outreach=outreach, founder=founder, reply=reply, thread_id="thread-401")


@pytest.fixture
def fake_classifier():
    from tests.fakes import FakeReplyClassifier

    return FakeReplyClassifier()


@pytest.fixture
def exploding_classifier():
    from tests.fakes import ExplodingReplyClassifier

    return ExplodingReplyClassifier()


@pytest.fixture
def flaky_classifier():
    from tests.fakes import FlakyReplyClassifier

    return FlakyReplyClassifier()


@pytest.fixture
def all_active_jobs_client(repos):
    """Fix round 1 (T6-1): a `queued` row with NO `started_at` at all, and
    a `running` row started only an instant ago - the two shapes the old
    age-gated `recover_stale` could never touch (a `None` `started_at`
    fails any age comparison; a fresh `running` row is never "old"). Both
    must come back `failed`/`interrupted` after boot now that startup
    recovery is unconditional."""
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from app.api.main import create_app

    repos.job.upsert(
        kind="draft", status="queued", created_by="ui",
        started_at=None, done=0, total=None, summary=None, error=None, finished_at=None,
    )
    repos.job.upsert(
        kind="scrape", status="running", created_by="ui",
        started_at=datetime.now(timezone.utc), done=0, total=None,
        summary=None, error=None, finished_at=None,
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
