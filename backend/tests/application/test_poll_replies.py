"""Replies land as inbound `messages` rows. Re-polling must be free, an
autoresponder must not end an outreach, and a bounce must be visible.

Beyond the brief's own six tests (Task 8's handover note: "Add tests
beyond the brief's" — four real behaviours went unpinned when Task 7
shipped its brief tests verbatim), this file also pins:

- a bounce whose display name/local-part merely CONTAINS "postmaster"
  must NOT be treated as a bounce (substring matching would wrongly catch
  a founder legitimately named that way);
- a reply on a thread that already has two outbound steps (a follow-up
  sent) still resolves to the one right outreach;
- an empty poll (nothing new) is a normal, zero-count run, not an error;
- the history cursor is persisted only AFTER the messages it covers are
  committed (cursor-last ordering), not before.
"""
from datetime import datetime, timezone

import pytest

from app.application.poll_replies import PollReplies, PollRepos
from app.domain.states import OutreachState as S
from app.ports import GmailAuthRequired, GmailError, GmailHistoryExpired


def _repos(repos) -> PollRepos:
    return PollRepos(
        outreach=repos.outreach,
        message=repos.message,
        founder=repos.founder,
        credential=repos.credential,
        session=repos.session,
    )


class _FixedClock:
    def now(self):
        return datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _clock() -> _FixedClock:
    return _FixedClock()


def test_a_reply_on_a_thread_we_sent_is_stored_as_inbound(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1",
                    sender="pratish@onefin.in", body="Sure, send it over.")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.stored == 1
    row = repos.message.list_by_outreach(sent_outreach.outreach.id)[-1]
    assert row.direction == "inbound" and row.gmail_message_id == "m1"
    assert row.label is None            # classification is a separate step


def test_mail_on_an_unknown_thread_is_ignored(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id="not-ours", message_id="x1", sender="spam@x.com", body="hi")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.stored == 0 and result.skipped == 1


def test_polling_the_same_message_twice_stores_it_once(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    p = PollReplies(fake_reader, _repos(repos), _clock())
    p.run()
    fake_reader.rewind()                 # same window again
    assert p.run().stored == 0


def test_an_expired_history_id_falls_back_to_a_recent_window(repos, sent_outreach, fake_reader):
    fake_reader.expire_history()
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    assert PollReplies(fake_reader, _repos(repos), _clock()).run().stored == 1
    assert fake_reader.used_fallback is True


def test_the_history_cursor_is_advanced_and_persisted(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="ok")
    PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert repos.credential.get_by_account_email("me@gmail.com").last_history_id == fake_reader.history_id


def test_a_bounce_marks_the_outreach_and_is_never_classified(repos, sent_outreach, fake_reader):
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="b1",
                    sender="mailer-daemon@googlemail.com", body="Address not found")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.bounced == 1
    assert repos.outreach.get(sent_outreach.outreach.id).state == S.BOUNCED
    assert repos.message.list_by_outreach(sent_outreach.outreach.id)[-1].label == "BOUNCE"


def test_polling_does_not_change_state_on_its_own(repos, sent_outreach, fake_reader):
    # Classification, not polling, is what moves a row to REPLIED.
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert repos.outreach.get(sent_outreach.outreach.id).state == S.SENT


# --- Added beyond the brief -----------------------------------------------


def test_a_display_name_merely_containing_postmaster_is_not_a_bounce(repos, sent_outreach, fake_reader):
    """`postmaster` must be matched as the address's exact local part, not
    as a substring anywhere in the header — a founder whose address
    happens to contain "postmaster" (e.g. a personal branding address)
    must never be misdetected as a bounce."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1",
                    sender="founder-postmaster@startup.com", body="Yes, let's talk.")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.bounced == 0 and result.stored == 1
    assert repos.outreach.get(sent_outreach.outreach.id).state == S.SENT
    row = repos.message.list_by_outreach(sent_outreach.outreach.id)[-1]
    assert row.label is None


def test_a_reply_lands_on_the_right_outreach_when_two_steps_were_sent(repos, sent_outreach, fake_reader):
    """A thread with two outbound steps (an initial send plus a follow-up)
    still resolves to exactly one outreach — `OutreachRepo.get_by_thread_id`
    must not get confused by, or double-count, the second outbound row."""
    repos.message.upsert(
        outreach_id=sent_outreach.outreach.id, sequence_step=1, direction="outbound",
        subject="Following up", body="Just bumping this.",
        gmail_message_id="sent-2", gmail_thread_id=sent_outreach.thread_id,
    )
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1",
                    sender="pratish@onefin.in", body="Sorry for the delay, yes!")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.stored == 1
    row = repos.message.list_by_outreach(sent_outreach.outreach.id)[-1]
    assert row.gmail_message_id == "m1"
    assert repos.outreach.get(sent_outreach.outreach.id).id == sent_outreach.outreach.id


def test_an_empty_poll_stores_nothing_and_errors_nothing(repos, sent_outreach, fake_reader):
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result == type(result)(fetched=0, stored=0, bounced=0, skipped=0)
    # Only the fixture's own outbound draft row exists — nothing new landed.
    rows = repos.message.list_by_outreach(sent_outreach.outreach.id)
    assert [r.direction for r in rows] == ["outbound"]


def test_the_cursor_is_persisted_only_after_messages_are_committed(repos, sent_outreach, fake_reader):
    """Cursor-last ordering (Task 8 handover): if committing a message
    were to fail partway through a run, the cursor must not have moved
    yet. Modelled here by making the session's `commit` raise on message
    writes; the credential's cursor must still show its pre-run value
    afterward, proving the cursor write is ordered after (and therefore
    never reached before) the message commits."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="hi")
    before = repos.credential.get_by_account_email("me@gmail.com").last_history_id

    real_commit = repos.session.commit
    calls = []

    def spying_commit():
        calls.append(repos.credential.get_by_account_email("me@gmail.com").last_history_id)
        real_commit()

    repos.session.commit = spying_commit
    PollReplies(fake_reader, _repos(repos), _clock()).run()

    # The FIRST commit (the message write) must see the cursor still at
    # its pre-run value; only the LAST commit (after set_history_id) sees
    # it advanced.
    assert calls[0] == before
    assert calls[-1] == fake_reader.history_id


# --- Fix round 1 (ruling T8-1): exceptions caught by type, not by name -----


def test_the_adapters_gmail_exceptions_are_the_same_objects_as_ports(repos, sent_outreach, fake_reader):
    """Pins the fix itself: `PollReplies` must catch these by `isinstance`
    against `app.ports`, not by comparing `type(exc).__name__` — a rename
    of the adapter's own re-exported alias can never silently turn a
    recognised, handled failure into an unhandled crash mid-poll (exactly
    the class of bug T4-1 already ruled out elsewhere in this codebase),
    because there is only ever ONE class object, not two same-named ones."""
    import app.ports as ports
    from app.adapters.gmail import GmailError as AdapterGmailError
    from app.adapters.gmail_reader import GmailHistoryExpired as AdapterGmailHistoryExpired

    assert AdapterGmailHistoryExpired is ports.GmailHistoryExpired
    assert AdapterGmailError is ports.GmailError


def test_an_expired_cursor_raised_as_the_real_ports_type_is_handled(repos, sent_outreach, fake_reader):
    """Same behaviour as `test_an_expired_history_id_falls_back_to_a_recent_window`
    above, but pins that the fallback is reached via `except
    GmailHistoryExpired:` catching the REAL `app.ports` type the fake now
    raises (fix round 1) — not a name-alike stand-in."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    with pytest.raises(GmailHistoryExpired):
        fake_reader.expire_history()
        fake_reader.history_since("100")  # sanity: the fake really raises the real ports type
    fake_reader.expire_history()  # expire_history() fires once per call; arm it again for the real run
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.stored == 1
    assert fake_reader.used_fallback is True


def test_a_gmail_error_mid_run_ends_the_run_cleanly(repos, sent_outreach, fake_reader):
    """A `GmailError` (real `app.ports` type) raised by the reader while
    fetching what's new must not propagate out of `PollReplies.run()` —
    the run ends cleanly with whatever partial result it has (here, none),
    and the CURSOR IS NOT ADVANCED, so the next scheduled poll retries this
    same window rather than skipping it."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    before = repos.credential.get_by_account_email("me@gmail.com").last_history_id

    with pytest.raises(GmailError):
        fake_reader.fail_with_gmail_error()
        fake_reader.history_since("100")  # sanity: the fake really raises the real ports type
    fake_reader.fail_with_gmail_error()  # arm it again — it fires once per call

    result = PollReplies(fake_reader, _repos(repos), _clock()).run()  # must not raise
    assert result.stored == 0
    assert repos.credential.get_by_account_email("me@gmail.com").last_history_id == before


# --- Fix round 1: pin the bounce-outside-SENT decision (was disclosed, untested) --


def test_a_bounce_on_an_outreach_not_in_sent_is_stored_but_state_is_left_alone(
    repos, sent_outreach, fake_reader,
):
    """Decision, not an accident (flagged in the Task 8 report as an
    untested disclosed guess): a bounce arriving while the outreach is
    already past `SENT` (here, `REPLIED` — a founder replied normally,
    then a LATER message on the same thread bounces, e.g. a stale CC)
    cannot legally transition `REPLIED -> BOUNCED`
    (`app.domain.states._LEGAL` has no such edge). `PollReplies` stores
    the row anyway — labelled `BOUNCE`, still counted, still never
    reachable by the classifier — but leaves the outreach's state exactly
    as it was, rather than raising `IllegalTransition` and losing the rest
    of the run."""
    repos.outreach.upsert(founder_id=sent_outreach.founder.id, state=str(S.REPLIED))
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="b1",
                    sender="mailer-daemon@googlemail.com", body="Address not found")

    result = PollReplies(fake_reader, _repos(repos), _clock()).run()

    assert result.bounced == 1
    assert repos.outreach.get(sent_outreach.outreach.id).state == S.REPLIED
    assert repos.message.list_by_outreach(sent_outreach.outreach.id)[-1].label == "BOUNCE"


# --- Fix round 2 (review): mutation-surviving gaps -------------------------


def test_a_non_gmail_exception_propagates_out_of_run(repos, sent_outreach, fake_reader):
    """`run()` must catch ONLY `GmailError` — widening that to a bare
    `except Exception:` would turn every genuine bug in a poll (a repo
    error, an attribute typo, anything) into a silently "clean" run
    reporting zero replies. A `RuntimeError` raised mid-run (here, from a
    broken `credential` repo) must escape `run()` unchanged."""

    class _BrokenCredentialRepo:
        def get_by_account_email(self, account_email):
            raise RuntimeError("boom: not a Gmail failure")

        def set_history_id(self, account_email, history_id):
            raise AssertionError("should never be reached")

    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="hi")
    broken_repos = _repos(repos)
    broken_repos.credential = _BrokenCredentialRepo()

    with pytest.raises(RuntimeError, match="boom: not a Gmail failure"):
        PollReplies(fake_reader, broken_repos, _clock()).run()


def test_the_fallback_persists_a_fresh_cursor_not_the_stale_one(repos, sent_outreach, fake_reader):
    """After an expired-history fallback, the persisted cursor must be the
    reader's FRESH id (`current_history_id()`), never the stale one that
    just expired — persisting the stale value would make every future poll
    fall back again forever, re-scanning a 14-day window each time instead
    of ever resuming from `history_since` again."""
    stale_cursor = repos.credential.get_by_account_email("me@gmail.com").last_history_id
    fake_reader.expire_history()
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")

    PollReplies(fake_reader, _repos(repos), _clock()).run()

    persisted = repos.credential.get_by_account_email("me@gmail.com").last_history_id
    assert persisted == fake_reader.current_history_id()
    assert persisted != stale_cursor


def test_a_re_poll_of_the_same_window_never_calls_get_message_again(repos, sent_outreach, fake_reader):
    """Dedupe-before-fetch (module docstring point 3): `get_by_gmail_id`
    must be checked BEFORE `reader.get_message(id)` is ever called for a
    known id — not just "no duplicate row ends up stored". Proven here by
    counting the fake reader's actual `get_message` calls across a rewound
    re-poll, which must not grow."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    p = PollReplies(fake_reader, _repos(repos), _clock())

    p.run()
    calls_after_first_run = fake_reader.get_message_calls
    assert calls_after_first_run == 1

    fake_reader.rewind()  # same window again
    p.run()

    assert fake_reader.get_message_calls == calls_after_first_run  # no new fetch burned


def test_a_reader_with_no_account_fails_fast_at_construction(repos):
    """`getattr(reader, "account", None)` silently defaulting to `None`
    would go on to look up (and, worse, later persist a cursor for) a
    credential row keyed on account `None` — a confusing failure far from
    its actual cause. `PollReplies` must instead refuse to construct at
    all when the reader has no non-empty `.account`."""

    class _AccountlessReader:
        pass  # no .account attribute at all

    with pytest.raises(ValueError, match="account"):
        PollReplies(_AccountlessReader(), _repos(repos), _clock())


# --- Final fix wave: F1 (own-address mail) and F2 (auth failure propagates) --


def test_the_operators_own_reply_on_a_thread_is_never_stored_or_classified(
    repos, sent_outreach, fake_reader,
):
    """F1: spec §5 "Replying" has the operator answer founders FROM
    GMAIL. That message lands on one of our threads, has no `messages`
    row (only `SendApproved`'s sends do), and is not a bounce - so
    without a sender check it was stored as an inbound reply, handed to
    the classifier as if a founder wrote it, and shown on the Thread
    screen as a founder "reply". It must be skipped: no row, no state
    change, and the classifier never sees it."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="own-1",
                    sender="me@gmail.com", body="Thanks Pratish - here is the resume again.")
    before = len(repos.message.list_by_outreach(sent_outreach.outreach.id))

    result = PollReplies(fake_reader, _repos(repos), _clock()).run()

    assert (result.stored, result.bounced, result.skipped) == (0, 0, 1)
    assert repos.message.get_by_gmail_id("own-1") is None
    assert len(repos.message.list_by_outreach(sent_outreach.outreach.id)) == before
    assert repos.outreach.get(sent_outreach.outreach.id).state == str(S.SENT)


def test_the_own_address_check_is_case_insensitive(repos, sent_outreach, fake_reader):
    """Gmail may render the operator's address with different casing in
    the `From` header than `GMAIL_SENDER_ADDRESS` was typed with; a
    case-sensitive comparison would let the operator's own reply through
    on exactly the accounts where the setting was typed in caps."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="own-2",
                    sender="ME@Gmail.com", body="my own words")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert result.stored == 0 and result.skipped == 1
    assert repos.message.get_by_gmail_id("own-2") is None


def test_a_founder_reply_is_still_stored_when_the_operator_also_replied(
    repos, sent_outreach, fake_reader,
):
    """The own-address skip must be exact: it drops ONLY the operator's
    message, not the whole thread. The founder's reply in the same poll
    window is stored as before."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="own-3",
                    sender="me@gmail.com", body="Sure, attached.")
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="founder-3",
                    sender="pratish@onefin.in", body="Got it, thanks!")
    result = PollReplies(fake_reader, _repos(repos), _clock()).run()
    assert (result.stored, result.skipped) == (1, 1)
    assert repos.message.get_by_gmail_id("own-3") is None
    assert repos.message.get_by_gmail_id("founder-3") is not None


def test_a_gmail_auth_failure_propagates_out_of_run(repos, sent_outreach, fake_reader):
    """F2 (spec §9): `GmailAuthRequired` - a 401, a revoked refresh token,
    no stored credential - is the one `GmailError` that must NOT be
    swallowed into a clean zero-result run: every later poll would fail
    the same way, silently, forever. It escapes `run()` unchanged (so the
    job runner records `failed / GmailAuthRequired`), and the cursor is
    not advanced."""
    fake_reader.add(thread_id=sent_outreach.thread_id, message_id="m1", sender="a@b.com", body="yes")
    before = repos.credential.get_by_account_email("me@gmail.com").last_history_id
    fake_reader.fail_with_auth_required()

    with pytest.raises(GmailAuthRequired) as exc_info:
        PollReplies(fake_reader, _repos(repos), _clock()).run()

    assert isinstance(exc_info.value, GmailError)  # still a GmailError, just the fatal subtype
    assert repos.credential.get_by_account_email("me@gmail.com").last_history_id == before
    assert repos.message.get_by_gmail_id("m1") is None
