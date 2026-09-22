"""Task 20: one offline pass through the whole Phase 2 loop, on the fakes -
draft -> approve over HTTP -> send -> reply arrives -> classified ->
follow-up scheduled or cancelled, never touching a database or the network.

The brief (`task-20-brief.md`) was written before Tasks 1-19 existed, so its
literal fixture names (`ready_founder.outreach`, a bare `_draft_repos(repos)`
helper, etc.) do not match what actually shipped. Every adaptation below
keeps the INTENT of the brief's own assertions and is called out in its own
comment; the report lists them again as a single list.

Global constraint (task-20 instructions): the loop is driven ONLY through
public entry points — HTTP routes via `client`, or a use case's own
`.run()`. The only repo writes anywhere in this file are inside fixtures
that set up state a real prior stage would already have produced (e.g. a
stored Gmail credential row from a completed `gmail-auth`), each marked
with a comment saying so — never inside a test body.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.classify_reply import ClassifyReply, ClassifyRepos
from app.application.draft_outreach import DraftOutreach
from app.application.poll_replies import PollReplies, PollRepos
from app.application.schedule_follow_ups import FollowUpRepos, ScheduleFollowUps
from app.domain.states import OutreachState as S
from tests.conftest import default_test_job_factories
from tests.fakes import FakeDrafter
from tests.fakes_repos import PENDING_BODY, PENDING_SUBJECT, VAHAN_DESCRIPTION, VAHAN_NEWS

# `client` lives in tests/api/conftest.py, not the root conftest, and this
# file sits at tests/ root (the brief's own path) rather than tests/api/ (it
# drives application use cases directly alongside the HTTP routes, which
# tests/api/ files don't do) — so it is redefined here, identically, rather
# than moving this file under tests/api/ or reaching into a sibling
# package's conftest.


@pytest.fixture
def client(repos):
    session_factory, runner_factory = default_test_job_factories(repos)
    app = create_app(
        repos_factory=lambda: repos,
        job_session_factory=session_factory,
        job_runner_factory=runner_factory,
    )
    with TestClient(app) as c:
        yield c

# --- repo-bundle helpers ------------------------------------------------------
# Same pattern each application test file already uses (e.g.
# tests/application/test_poll_replies.py's `_repos`, test_classify_reply.py's
# `_repos`, test_schedule_follow_ups.py's `_repos`) — the fake `repos` bundle
# (`FakeRepos`) is a structural superset of each use case's own *Repos
# dataclass, so these just slice out the fields each use case declares.
# `DraftOutreach` is the one exception: every existing test (test_draft_
# outreach.py, test_draft_followup.py) hands it the `repos` bundle directly,
# with no wrapper, so this file follows that same convention rather than
# inventing a `_draft_repos` no other test uses.


def _poll_repos(repos) -> PollRepos:
    return PollRepos(outreach=repos.outreach, message=repos.message, founder=repos.founder,
                     credential=repos.credential, session=repos.session)


def _classify_repos(repos) -> ClassifyRepos:
    return ClassifyRepos(message=repos.message, outreach=repos.outreach, session=repos.session,
                         settings=repos.settings)


def _follow_repos(repos) -> FollowUpRepos:
    return FollowUpRepos(outreach=repos.outreach, message=repos.message, session=repos.session,
                         settings=repos.settings)


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def gmail_credential(repos):
    """SETUP ONLY (repo poke, commented per task-20 rule): a stored Gmail
    credential row for "me@gmail.com" — exactly what a completed `gmail-auth`
    run would already have produced before any poll ever runs.
    `PollReplies`/`FakeCredentialRepo.set_history_id` requires an existing
    row (mirrors the real schema's NOT NULL `refresh_token`, Task 8 handover
    note (a)); `fake_reader`'s default `.account` is "me@gmail.com", so this
    is the row it needs. `last_history_id=None` deliberately — the reply
    lands on this thread's first-ever poll, which takes the
    `recent_message_ids()` fallback path, exactly like a founder's first
    reply would in production."""
    repos.credential.upsert(account_email="me@gmail.com", refresh_token="refresh-token-e2e",
                            last_history_id=None)


@pytest.fixture
def ready_founder_with_address(repos, ready_founder):
    """SETUP ONLY (repo poke, commented per task-20 rule): `ready_founder`
    (root conftest) is `READY_TO_DRAFT` but was never resolved an email
    address — that is `ResolveEmails`'s job, a separate stage not under
    test here (§4's job pipeline runs sync -> scrape -> resolve -> draft in
    order; this file starts mid-pipeline, at draft). Stamping
    `resolved_email` directly is standing in for a resolve run that already
    happened, exactly as `_add_founder_with_draft`/`_add_pending_founder`
    (tests/fakes_repos.py) already do for every other fixture in this
    codebase that starts past `NEEDS_EMAIL`."""
    outreach = repos.outreach.get_by_founder_id(ready_founder.id)
    outreach.resolved_email = "founder@example.com"
    return ready_founder


@pytest.fixture
def sent_founder_with_material(repos, clock):
    """SETUP ONLY (repo poke, commented per task-20 rule): a `SENT`, step-0
    founder with REAL research material — same shape as Task 12's
    `ready_follow_up` fixture (Vahan-style company, one news item, a step-0
    body that already validates) but starting one step earlier, at `SENT`
    rather than pre-scheduled `READY_TO_DRAFT`, sent 5 days before `clock`'s
    "now" — so the e2e test itself can drive `ScheduleFollowUps` and then
    `DraftOutreach` through their own `.run()` calls, rather than starting
    from a state a use case under test should have produced. id=900
    throughout (company/founder/outreach/news), following this codebase's
    own id-reuse convention (see `tests/fakes_repos.py`'s module docstring)
    to keep the fixture self-contained and collision-free."""
    id = 900
    repos.company.add(id=id, domain="vahan900.ai", name="Vahan",
                      one_liner="Recruitment over voice", long_description=VAHAN_DESCRIPTION)
    founder = repos.founder.add(id=id, company_id=id, full_name="Madhav Krishna", title="Founder")
    outreach = repos.outreach.add(id=id, founder_id=founder.id, state=str(S.SENT))
    outreach.resolved_email = f"founder{id}@example.com"
    outreach.sequence_step = 0
    sent_at = clock.now() - timedelta(days=5)
    repos.message.upsert(
        outreach_id=outreach.id, sequence_step=0, direction="outbound",
        subject=PENDING_SUBJECT, body=PENDING_BODY,
        research_source_type="news", research_source_id=id,
        approved_at=sent_at, sent_at=sent_at,
        gmail_message_id=f"sent-{id}-0", gmail_thread_id=f"thread-{id}",
    )
    repos.news.add(id=id, company_id=id, title=VAHAN_NEWS, source="The Economic Times")
    return SimpleNamespace(outreach=outreach, founder=founder)


# The follow-up draft `sent_founder_with_material`'s scheduled step-1 row
# validates against: shorter than the step-0 body (spec's follow-up-must-
# be-shorter rule), same shape as `test_draft_followup.py`'s own
# `GOOD_FOLLOW_UP`, retargeted at this fixture's own news id (900).
GOOD_FOLLOW_UP = {
    "subject": "Following up, Vahan intern",
    "body": ("Circling back on the voice routing note.\n\n"
             "Still keen to help build it.\n\n"
             "Would you take an intern?"),
    "research_source_type": "news", "research_source_id": 900,
}


# --- the brief's two tests, adapted --------------------------------------------


def test_a_reply_cancels_the_follow_up_sequence(client, repos, fake_reader, fake_classifier,
                                                fake_sender, clock, ready_founder_with_address,
                                                fake_drafter, gmail_credential):
    """Adapted from the brief verbatim, floor kept: draft -> approve over
    HTTP -> send -> reply arrives -> classified INTERESTED -> follow-ups
    cancelled -> the thread renders the labelled reply.

    Adaptations (see report for the full list):
    - `ready_founder` returns a bare `FakeFounderRow`, not a
      `.outreach`-carrying wrapper — the outreach id is looked up via
      `repos.outreach.get_by_founder_id(...)`; it also carries no resolved
      email (a separate, earlier stage — `ready_founder_with_address` wraps
      it and stamps one, standing in for a completed resolve run).
    - `PollReplies`/`ClassifyReply`/`ScheduleFollowUps` take the real
      `*Repos` dataclasses (`PollRepos`/`ClassifyRepos`/`FollowUpRepos`),
      built by the `_poll_repos`/`_classify_repos`/`_follow_repos` helpers
      above, not the brief's invented `_poll_repos`/`_classify_repos`/
      `_follow_repos(repos)` (same names, but they don't exist anywhere in
      the codebase until this file) — matching the actual application test
      files' own convention.
    - `PollReplies` additionally needs a stored Gmail credential row
      (`gmail_credential` fixture) before it will poll at all — not
      mentioned in the brief.
    - `outreach.state.value == "replied"` -> `outreach.state == S.REPLIED`
      (an `OutreachState` `StrEnum` compares equal either way; this file
      follows `test_draft_outreach.py`'s own convention).
    - `fake_reader.add(...)` needs an explicit `received_at` pinned to a
      real "now" (`POST /api/send` timestamps with a real UTC clock, not
      the fixture `clock`) — without it the thread-order assertion is a
      flake depending on when the suite happens to run relative to the
      reader's fixed 2026-09-14 default.
    """
    DraftOutreach(fake_drafter, repos).run(limit=1)
    outreach_id = repos.outreach.get_by_founder_id(ready_founder_with_address.id).id
    assert client.post(f"/api/outreach/{outreach_id}/approve").status_code == 200
    assert client.post("/api/send", json={"limit": 1}).json()["sent"] == 1

    thread_id = repos.message.get_outbound(outreach_id, 0).gmail_thread_id
    # `POST /api/send` stamps `sent_at` from a real UTC clock (the router's
    # own `_UTCClock`, not the fixture `clock`), so the reply's
    # `received_at` must be pinned to a real "now" too — otherwise
    # `FakeGmailReader.add`'s fixed 2026-09-14 default could sort BEFORE
    # the just-sent outbound row instead of after it, depending on when
    # this suite happens to run.
    fake_reader.add(thread_id=thread_id, message_id="r1", sender="founder@example.com",
                    body="Sure, let's talk Friday.", received_at=datetime.now(timezone.utc))
    PollReplies(fake_reader, _poll_repos(repos), clock).run()
    fake_classifier.next = {"label": "INTERESTED", "confidence": 0.9, "rationale": "wants to talk"}
    ClassifyReply(fake_classifier, _classify_repos(repos), clock).run()

    outreach = repos.outreach.get(outreach_id)
    assert outreach.state == S.REPLIED
    assert outreach.next_action_at is None
    assert ScheduleFollowUps(_follow_repos(repos), clock).run().scheduled == 0

    thread = client.get(f"/api/outreach/{outreach_id}/thread").json()
    assert [m["direction"] for m in thread["messages"]] == ["outbound", "inbound"]
    assert thread["messages"][1]["label"] == "INTERESTED"


def test_no_reply_produces_a_follow_up_in_the_approval_queue(client, repos, clock,
                                                              sent_founder_with_material):
    """Adapted from the brief verbatim, floor kept: a due `SENT` row with no
    reply gets scheduled to step 1, drafted, and lands back in the review
    queue — still gated on a human (nothing here sends anything).

    Adaptations (see report for the full list):
    - the brief's `sent_five_days_ago`/`fake_drafter` fixtures produce a
      founder with NO research material past the step-0 body, so
      `DraftOutreach` would take the no-material manual-write path and
      write no step-1 message row at all — the brief's own final assertion
      (`repos.message.get_outbound(..., 1).sent_at is None`) would then
      crash on `None.sent_at`. `sent_founder_with_material` (this file) is
      a `SENT` founder that DOES carry real material, mirroring Task 12's
      `ready_follow_up` fixture one step earlier, so `DraftOutreach`
      actually drafts step 1.
    - a step-1 follow-up must be SHORTER than the step-0 body (the
      follow-up shrink rule, `validation.py`), so the shared root
      `fake_drafter` fixture (which repeats the same, longer, step-0-shaped
      body) cannot be reused here; `GOOD_FOLLOW_UP` above is a validating,
      genuinely shorter follow-up body, matching
      `test_draft_followup.py`'s own fixture.
    - `fake_sender` was named in the brief's signature but never used in
      its own body (no `/api/send` call happens in this test) — dropped.
    """
    result = ScheduleFollowUps(_follow_repos(repos), clock).run()
    assert result.scheduled == 1
    assert repos.outreach.get(sent_founder_with_material.outreach.id).state == S.READY_TO_DRAFT

    DraftOutreach(FakeDrafter([GOOD_FOLLOW_UP]), repos).run(limit=1)

    item = client.get("/api/queue").json()[0]
    assert item["sequence_step"] == 1
    # Still gated on a human.
    assert repos.message.get_outbound(sent_founder_with_material.outreach.id, 1).sent_at is None


# --- beyond the brief: task-20's own required additions -----------------------


def test_a_machine_reply_does_not_cancel_the_sequence(repos, fake_reader, fake_classifier, clock,
                                                       sent_five_days_ago, gmail_credential):
    """(a) A machine reply (AUTO_REPLY) must never be able to end an
    outreach (spec §5's central rule, already pinned at the unit level in
    Task 9 — this is the same guarantee re-proven end to end): the outreach
    stays `SENT`, so a later `ScheduleFollowUps` run still schedules the
    follow-up exactly as if no reply had ever arrived.

    `sent_five_days_ago` (Task 11's own root fixture) is a `SENT`, step-0
    row already sent exactly 5 days before `clock`'s "now" — due for a
    follow-up under the default `followup_day_1=5` the moment nothing
    blocks it."""
    outreach_id = sent_five_days_ago.outreach.id
    thread_id = repos.message.get_outbound(outreach_id, 0).gmail_thread_id
    fake_reader.add(thread_id=thread_id, message_id="auto1", sender="founder@example.com",
                    body="I'm out of office until next week.")
    PollReplies(fake_reader, _poll_repos(repos), clock).run()

    fake_classifier.next = {"label": "AUTO_REPLY", "confidence": 0.8, "rationale": "autoresponder"}
    ClassifyReply(fake_classifier, _classify_repos(repos), clock).run()
    assert repos.outreach.get(outreach_id).state == S.SENT   # machine label changed nothing

    result = ScheduleFollowUps(_follow_repos(repos), clock).run()
    assert result.scheduled == 1
    assert repos.outreach.get(outreach_id).state == S.READY_TO_DRAFT
    assert repos.outreach.get(outreach_id).sequence_step == 1


def test_a_bounce_marks_the_outreach_bounced_and_no_follow_up_schedules(
    repos, fake_reader, clock, sent_five_days_ago, gmail_credential,
):
    """(b) A bounce (sender's local part `mailer-daemon`/`postmaster`,
    Task 8) is detected by `PollReplies` itself, before classification ever
    runs: `SENT -> BOUNCED`. A bounced row must never re-enter the
    follow-up sequence."""
    outreach_id = sent_five_days_ago.outreach.id
    thread_id = repos.message.get_outbound(outreach_id, 0).gmail_thread_id
    fake_reader.add(thread_id=thread_id, message_id="bounce1",
                    sender="mailer-daemon@example.com", body="Delivery failed.")
    result = PollReplies(fake_reader, _poll_repos(repos), clock).run()
    assert result.bounced == 1
    assert repos.outreach.get(outreach_id).state == S.BOUNCED

    assert ScheduleFollowUps(_follow_repos(repos), clock).run().scheduled == 0
    assert repos.outreach.get(outreach_id).state == S.BOUNCED
