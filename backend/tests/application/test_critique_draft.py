"""Task 13 (spec: "The voice critic (D22)"). The first six tests are the
brief's own, verbatim.

The single rule that defines this task: the critic is advisory only (D22).
It can never block a send and can never approve one. It only ever adds
information for the human reviewer. Whatever it says, the draft reaches
PENDING_APPROVAL. A critic that crashes must not stop a draft either.

Beyond the brief's own six tests, this file pins the rules the brief
describes in prose but does not itself exercise directly: the critic's
retry budget is the same one the deterministic validator uses (not
additive — never more than `_MAX_ATTEMPTS` drafter calls total, and the
critic never even runs on a draft that hasn't cleared the deterministic
validator yet); a critic exception is recorded honestly rather than
faked as a PASS; a critic-triggered redraft that then fails the
deterministic validator still carries the critic's last verdict to the
review queue; the critic's `concerns` actually reach the redraft prompt;
and the critic is shown the validated draft, not raw, unvalidated model
output.

Deviation from the brief, disclosed: the brief's own bodies write
`ready_founder.outreach.id`; the established `ready_founder` fixture
(Task 14) returns a bare `FakeFounderRow` with no `.outreach` attribute,
matching every other Task-14 test's `repos.outreach.get(ready_founder.id)`
usage. Rather than reshape that fixture (which would break every
existing `test_draft_outreach.py` call site that already destructures it
as a plain founder row), this file uses `ready_founder.id` — numerically
identical to what `.outreach.id` would have been, since the fixture
constructs the founder and its outreach row with the same id (5).
"""
from app.application.draft_outreach import DraftOutreach, DraftRepos
from tests.fakes import FakeDrafter
from tests.fakes_repos import PENDING_BODY, PENDING_SUBJECT

PASS = {"verdict": "PASS", "concerns": [], "overall_note": "specific and short"}
REVISE = {"verdict": "NEEDS_REVISION",
          "concerns": [{"span": "excited about your work", "issue": "could be any company"}],
          "overall_note": "opener is generic"}


def _repos(repos) -> DraftRepos:
    return DraftRepos(
        founder=repos.founder, company=repos.company, news=repos.news, launch=repos.launch,
        job_posting=repos.job_posting, outreach=repos.outreach, message=repos.message,
        resume_profile=repos.resume_profile,
    )


# --- Brief's own tests --------------------------------------------------------


def test_a_pass_verdict_is_stored_with_the_draft(repos, ready_founder, fake_drafter, fake_critic):
    fake_critic.next = PASS
    DraftOutreach(fake_drafter, _repos(repos), critic=fake_critic).run(limit=1)
    row = repos.message.get_outbound(ready_founder.id, 0)
    assert row.critic_verdict == "PASS" and row.critic_overall_note


def test_needs_revision_triggers_exactly_one_redraft(repos, ready_founder, fake_drafter, fake_critic):
    fake_critic.queue = [REVISE, PASS]
    DraftOutreach(fake_drafter, _repos(repos), critic=fake_critic).run(limit=1)
    assert len(fake_drafter.calls) == 2
    assert fake_drafter.calls[1]["critic_concerns"]


def test_a_second_needs_revision_still_reaches_the_human(repos, ready_founder, fake_drafter, fake_critic):
    fake_critic.queue = [REVISE, REVISE]
    DraftOutreach(fake_drafter, _repos(repos), critic=fake_critic).run(limit=1)
    assert len(fake_drafter.calls) == 2                 # never more than one critic retry
    assert repos.outreach.get(ready_founder.id).state.value == "pending_approval"
    assert repos.message.get_outbound(ready_founder.id, 0).critic_verdict == "NEEDS_REVISION"


def test_the_critic_redraft_is_revalidated_deterministically(repos, ready_founder, drafter_bad_then_good, fake_critic):
    # A tone fix must not be allowed to smuggle in a banned phrase.
    fake_critic.queue = [REVISE, PASS]
    result = DraftOutreach(drafter_bad_then_good, _repos(repos), critic=fake_critic).run(limit=1)
    assert result.manual_write + result.drafted == 1


def test_a_critic_failure_never_blocks_the_draft(repos, ready_founder, fake_drafter, exploding_critic):
    DraftOutreach(fake_drafter, _repos(repos), critic=exploding_critic).run(limit=1)
    assert repos.outreach.get(ready_founder.id).state.value == "pending_approval"


def test_without_a_critic_behaviour_is_unchanged(repos, ready_founder, fake_drafter):
    DraftOutreach(fake_drafter, _repos(repos)).run(limit=1)
    assert repos.message.get_outbound(ready_founder.id, 0).critic_verdict is None


# --- Guards beyond the brief ---------------------------------------------------


def test_critic_never_runs_when_the_deterministic_validator_fails_twice(
    repos, ready_founder, fake_critic,
):
    """The critic's retry budget is shared with the deterministic
    validator's, not additive. A draft that never passes deterministic
    validation must never reach the critic at all — and the whole run
    must still bottom out at exactly `_MAX_ATTEMPTS` (2) drafter calls,
    not 2 (validator) + up to 2 more (critic)."""
    bad = {"subject": PASS["overall_note"], "body": "I hope this email finds you well.",
           "research_source_type": "news", "research_source_id": 1}
    d = FakeDrafter([bad, bad])
    result = DraftOutreach(d, _repos(repos), critic=fake_critic).run(limit=1)
    assert len(d.calls) == 2
    assert fake_critic.calls == []
    assert result.manual_write == 1
    row = repos.message.get_outbound(ready_founder.id, 0)
    assert row.critic_verdict is None


def test_a_critic_exception_is_recorded_honestly_never_as_a_fabricated_pass(
    repos, ready_founder, fake_drafter, exploding_critic,
):
    DraftOutreach(fake_drafter, _repos(repos), critic=exploding_critic).run(limit=1)
    row = repos.message.get_outbound(ready_founder.id, 0)
    assert row.critic_verdict != "PASS"
    assert row.critic_verdict is None or row.critic_verdict == "ERROR"
    assert len(exploding_critic.calls) == 1     # the critic ran exactly once, never retried on its own failure
    assert len(fake_drafter.calls) == 1         # a critic exception must not spend the shared retry budget


def test_needs_revision_redraft_that_then_fails_validation_keeps_the_critics_last_verdict(
    repos, ready_founder, fake_critic,
):
    """First attempt validates and the critic asks for a revision; the
    re-draft (the model's tone fix) then fails deterministic validation
    (a banned phrase slipped in). The row must still land as needs-manual-
    write (`errors` non-empty) with the critic's earlier NEEDS_REVISION
    verdict on it — not silently wiped because the final attempt never
    reached the critic again."""
    good = {"subject": "Backend intern at Vahan",
            "body": ("You're matching blue-collar workers to jobs over voice at Vahan. "
                     "That routing problem is what I keep thinking about.\n\n"
                     "I built a scheduler in Python.\n\n"
                     "Would you take an intern?"),
            "research_source_type": "news", "research_source_id": 1}
    tone_fix_but_banned = {**good, "body": "I hope this email finds you well. Would you take an intern?"}
    d = FakeDrafter([good, tone_fix_but_banned])
    fake_critic.queue = [REVISE]

    result = DraftOutreach(d, _repos(repos), critic=fake_critic).run(limit=1)

    assert len(d.calls) == 2
    assert result.manual_write == 1
    assert result.drafted == 0
    row = repos.message.get_outbound(ready_founder.id, 0)
    assert row.critic_verdict == "NEEDS_REVISION"
    assert row.critic_concerns == REVISE["concerns"]


def test_critic_concerns_appear_verbatim_in_the_redraft_prompt(repos, ready_founder, fake_drafter, fake_critic):
    fake_critic.queue = [REVISE, PASS]
    DraftOutreach(fake_drafter, _repos(repos), critic=fake_critic).run(limit=1)
    assert fake_drafter.calls[1]["critic_concerns"] == REVISE["concerns"]
    # And the first attempt's context carried no critic_concerns at all —
    # only the retry does.
    assert "critic_concerns" not in fake_drafter.calls[0]


def test_critic_sees_the_validated_draft_not_the_raw_model_output(repos, ready_founder, fake_critic):
    raw_with_extra_field = {
        "subject": PENDING_SUBJECT, "body": PENDING_BODY,
        "research_source_type": "news", "research_source_id": "1",  # string, not int, in the raw output
        "some_unexpected_field": "the drafter should never send this to the critic",
    }
    d = FakeDrafter([raw_with_extra_field])
    fake_critic.next = PASS

    DraftOutreach(d, _repos(repos), critic=fake_critic).run(limit=1)

    assert len(fake_critic.calls) == 1
    seen = fake_critic.calls[0]
    assert set(seen.keys()) == {"subject", "body", "research_source_type", "research_source_id"}
    assert seen["research_source_id"] == 1          # coerced to int by `_to_draft`, not the raw "1" string
    assert "some_unexpected_field" not in seen

