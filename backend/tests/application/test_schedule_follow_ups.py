"""Follow-ups at +5 and +12 days (spec §6). Nothing auto-sends: a scheduled
follow-up re-enters the same approval queue as a first email.

`ScheduleFollowUps` writes no message and sends nothing — it only moves a
due `SENT` row back to `READY_TO_DRAFT` (incrementing `sequence_step`) or,
past the last defined step, on to `CLOSED`. Drafting the follow-up itself
is Task 12's job.

The brief's six tests are verbatim. Everything after them pins the
properties a mutant could slip past without failing the brief's own suite:
the `sequence_step < 2` cap (a row already at step 2 must close, not get a
third follow-up), every excluded state (not just REPLIED/BOUNCED), a NULL
`next_action_at` never being treated as due, the CLOSED transition actually
going through `assert_transition` rather than a raw `outreach.state = ...`
write, and the reservation guard for a follow-up step (mirrored here too,
matching `test_send_approved.py`'s replacement of the lifted R-C guard).
"""

from datetime import timedelta

import pytest

from app.application.schedule_follow_ups import FollowUpRepos, ScheduleFollowUps
from app.domain.states import IllegalTransition, OutreachState as S


def _repos(repos) -> FollowUpRepos:
    return FollowUpRepos(outreach=repos.outreach, message=repos.message,
                         session=repos.session, settings=repos.settings)


# --- brief ---------------------------------------------------------------------


def test_a_due_first_email_is_scheduled_for_follow_up_one(repos, sent_five_days_ago, clock):
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.scheduled == 1
    outreach = repos.outreach.get(sent_five_days_ago.outreach.id)
    assert outreach.state == S.READY_TO_DRAFT and outreach.sequence_step == 1


def test_a_row_not_yet_due_is_left_alone(repos, sent_two_days_ago, clock):
    assert ScheduleFollowUps(_repos(repos), clock).run().scheduled == 0
    assert repos.outreach.get(sent_two_days_ago.outreach.id).state == S.SENT


def test_after_step_two_the_row_closes(repos, sent_step_two_due, clock):
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.closed == 1
    assert repos.outreach.get(sent_step_two_due.outreach.id).state == S.CLOSED


def test_a_replied_row_is_never_followed_up(repos, replied_outreach, clock):
    assert ScheduleFollowUps(_repos(repos), clock).run().scheduled == 0


def test_a_bounced_row_is_never_followed_up(repos, bounced_outreach, clock):
    assert ScheduleFollowUps(_repos(repos), clock).run().scheduled == 0


def test_the_intervals_come_from_settings_not_constants(repos, sent_five_days_ago, clock):
    # day_1=30 means a 5-day-old row is not due.
    assert ScheduleFollowUps(_repos(repos), clock, day_1=30).run().scheduled == 0


# --- beyond the brief: the LIVE settings-table read, not just the constructor --
#
# T11-2 (review): the test above only ever exercises the `day_1=30`
# constructor kwarg. Deleting `repos.settings.get()` from `_interval_days`
# entirely (always falling back to `self._day_1`/`self._day_2`) left the
# whole suite passing — the module docstring's central justification for
# the two-stage due-check design ("an operator can edit the settings row at
# any time... this run must honour the CURRENT configured interval") had no
# test pinning it. This one leaves the constructor at its defaults and
# writes to `repos.settings` instead — it fails if `_interval_days` ever
# stops reading the settings repo.


def test_a_live_settings_row_overrides_the_constructor_default(repos, sent_five_days_ago, clock):
    repos.settings.set(followup_day_1=30)
    # Constructor day_1 is left at its default (5); only the settings row
    # says 30. A 5-day-old row must NOT be scheduled if the settings read
    # is real.
    assert ScheduleFollowUps(_repos(repos), clock).run().scheduled == 0
    assert repos.outreach.get(sent_five_days_ago.outreach.id).state == S.SENT


def test_a_live_settings_row_also_governs_the_step_two_close_check(repos, sent_step_two_due, clock):
    repos.settings.set(followup_day_2=30)
    # sent_step_two_due was sent 15 days ago; a 30-day settings interval
    # is not yet due, even though the constructor default (12) would be.
    assert ScheduleFollowUps(_repos(repos), clock).run().closed == 0
    assert repos.outreach.get(sent_step_two_due.outreach.id).state == S.SENT


# --- beyond the brief: the sequence_step < 2 cap --------------------------------


def test_a_row_at_step_two_never_gets_a_third_follow_up(repos, sent_step_two_due, clock):
    """The mutation-surviving twin of `test_after_step_two_the_row_closes`:
    a mutant that dropped the `sequence_step < 2` branch and always
    scheduled would still leave `outreach.state` non-SENT, but this pins
    `scheduled == 0` specifically — the row must never re-enter drafting."""
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.scheduled == 0
    assert repos.outreach.get(sent_step_two_due.outreach.id).sequence_step == 2


# --- beyond the brief: every excluded state, not just REPLIED/BOUNCED ----------


def test_a_closed_row_is_never_reopened(repos, closed_outreach, clock):
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.scheduled == 0 and result.closed == 0
    assert repos.outreach.get(closed_outreach.outreach.id).state == S.CLOSED


def test_a_discarded_row_is_never_followed_up(repos, discarded_outreach, clock):
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.scheduled == 0 and result.closed == 0


def test_an_unresolved_row_is_never_followed_up(repos, unresolved_outreach, clock):
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.scheduled == 0 and result.closed == 0


# --- beyond the brief: NULL next_action_at is never due -------------------------


def test_a_null_next_action_at_is_never_due(repos, sent_with_no_next_action_at, clock):
    """A mutant that compared `next_action_at <= now` without first
    excluding `None` would crash outright on a real database (NULL
    comparisons never satisfy `<=`) but a naive Python fake could silently
    treat `None` as "always due" — this pins the correct behaviour either
    way."""
    result = ScheduleFollowUps(_repos(repos), clock).run()
    assert result.scheduled == 0 and result.closed == 0
    assert repos.outreach.get(sent_with_no_next_action_at.outreach.id).state == S.SENT


# --- beyond the brief: transitions go through assert_transition, not a raw write --


def test_scheduling_goes_through_assert_transition(repos, sent_five_days_ago, clock, monkeypatch):
    """A mutant that wrote `outreach.state = str(READY_TO_DRAFT)` directly
    (skipping the domain guard) would still pass every test above — this
    fails if `assert_transition` is never actually called."""
    import app.application.schedule_follow_ups as mod

    calls = []
    real = mod.assert_transition

    def spy(src, dst):
        calls.append((src, dst))
        return real(src, dst)

    monkeypatch.setattr(mod, "assert_transition", spy)
    ScheduleFollowUps(_repos(repos), clock).run()
    assert (S.SENT, S.READY_TO_DRAFT) in calls


def test_closing_goes_through_assert_transition(repos, sent_step_two_due, clock, monkeypatch):
    import app.application.schedule_follow_ups as mod

    calls = []
    real = mod.assert_transition

    def spy(src, dst):
        calls.append((src, dst))
        return real(src, dst)

    monkeypatch.setattr(mod, "assert_transition", spy)
    ScheduleFollowUps(_repos(repos), clock).run()
    assert (S.SENT, S.CLOSED) in calls


def test_an_illegal_transition_is_never_swallowed(repos, sent_five_days_ago, clock, monkeypatch):
    """If `assert_transition` ever refuses (a future state-machine change
    makes SENT -> READY_TO_DRAFT illegal), that must propagate, not be
    caught and silently skipped — the use case has no reason to hide a
    domain-guard refusal from its caller."""
    import app.application.schedule_follow_ups as mod

    def always_refuse(src, dst):
        raise IllegalTransition(f"{src} -> {dst} not allowed")

    monkeypatch.setattr(mod, "assert_transition", always_refuse)
    with pytest.raises(IllegalTransition):
        ScheduleFollowUps(_repos(repos), clock).run()


# --- beyond the brief: no draft/send here, and next_action_at is cleared -------


def test_scheduling_writes_no_message_and_sends_nothing(repos, sent_five_days_ago, clock):
    before = len(repos.message.rows)
    ScheduleFollowUps(_repos(repos), clock).run()
    assert len(repos.message.rows) == before


def test_scheduling_clears_next_action_at_until_the_follow_up_is_actually_sent(
    repos, sent_five_days_ago, clock
):
    """`next_action_at` is not re-derived here — `SendApproved` is the only
    place that stamps the NEXT one, once this step is actually drafted,
    approved and sent (exactly as it already does for step 0)."""
    ScheduleFollowUps(_repos(repos), clock).run()
    assert repos.outreach.get(sent_five_days_ago.outreach.id).next_action_at is None


def test_commits_once_per_scheduled_row(repos, sent_five_days_ago, clock):
    ScheduleFollowUps(_repos(repos), clock).run()
    assert repos.session.commit_calls == 1
