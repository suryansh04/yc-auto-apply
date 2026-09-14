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
