"""Task 12: a follow-up draft carries thread history and goes through the
same approval queue as a first email. `ready_follow_up` (root conftest) is
a `READY_TO_DRAFT` founder at `sequence_step=1` whose step-0 email already
went out 5 days ago."""

from app.application.draft_outreach import DraftOutreach
from tests.fakes import FakeDrafter

GOOD_FOLLOW_UP = {
    "subject": "Following up, Vahan intern",
    "body": ("Circling back on the voice routing note.\n\n"
             "Still keen to help build it.\n\n"
             "Would you take an intern?"),
    "research_source_type": "news", "research_source_id": 515,
}


def test_a_follow_up_prompt_carries_the_thread_history(repos, ready_follow_up):
    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    ctx = fake_drafter.calls[0]
    assert ctx["sequence_step"] == 1
    assert ctx["thread_history"][0]["body"]


def test_a_follow_up_draft_lands_in_the_same_approval_queue(repos, ready_follow_up):
    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    assert repos.outreach.get(ready_follow_up.outreach.id).state.value == "pending_approval"


def test_the_follow_up_row_is_written_at_its_own_step(repos, ready_follow_up):
    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    assert repos.message.get_outbound(ready_follow_up.outreach.id, 1) is not None
    assert repos.message.get_outbound(ready_follow_up.outreach.id, 0).body   # step 0 untouched


# --- Beyond the brief --------------------------------------------------------


def test_the_thread_history_entry_carries_the_step_0_body_verbatim(repos, ready_follow_up):
    from tests.fakes_repos import PENDING_BODY

    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    ctx = fake_drafter.calls[0]
    (entry,) = ctx["thread_history"]
    assert entry["direction"] == "outbound"
    assert entry["sequence_step"] == 0
    assert entry["body"] == PENDING_BODY


def test_thread_history_includes_an_inbound_auto_reply_if_one_exists(repos, ready_follow_up):
    from datetime import datetime, timezone

    repos.message.upsert(
        outreach_id=ready_follow_up.outreach.id, sequence_step=0, direction="inbound",
        body="Thanks for your note - this is an automated acknowledgement.",
        received_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    ctx = fake_drafter.calls[0]
    directions = [entry["direction"] for entry in ctx["thread_history"]]
    assert "inbound" in directions


def test_a_first_email_has_empty_thread_history(repos, ready_founder):
    fake_drafter = FakeDrafter([{
        "subject": "Backend intern at Vahan",
        "body": ("You're matching blue-collar workers to jobs over voice at Vahan. "
                 "That routing problem is what I keep thinking about.\n\n"
                 "I built a scheduler in Python.\n\n"
                 "Would you take an intern?"),
        "research_source_type": "news", "research_source_id": 1,
    }])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    assert fake_drafter.calls[0]["thread_history"] == []


def test_a_step_2_follow_up_must_be_shorter_than_the_step_1_follow_up_not_just_the_first_email(
    repos, ready_follow_up,
):
    # The step-0 body (PENDING_BODY) is longer than any follow-up here, so a
    # naive "shorter than the FIRST email" check would let this through. It
    # must be checked against the step-1 body specifically.
    step_1_body = "Quick follow up on Vahan."  # very short — sets a low bar
    from datetime import datetime, timezone

    repos.message.upsert(
        outreach_id=ready_follow_up.outreach.id, sequence_step=1, direction="outbound",
        subject="Following up", body=step_1_body,
        approved_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        gmail_message_id="sent-515-1", gmail_thread_id="thread-515",
    )
    repos.outreach.upsert(founder_id=ready_follow_up.founder.id, sequence_step=2)

    not_shorter_than_step_1 = {
        **GOOD_FOLLOW_UP,
        "body": ("Circling back once more on the voice routing idea we talked about.\n\n"
                 "Still keen to help build it out this summer.\n\n"
                 "Would you take an intern?"),
    }
    fake_drafter = FakeDrafter([not_shorter_than_step_1, not_shorter_than_step_1])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    assert any("shorter" in e for e in fake_drafter.calls[1]["validation_errors"])


def test_the_prompt_word_target_matches_follow_up_word_targets_for_the_step(repos, ready_follow_up):
    from app.adapters.gemini import build_draft_prompt
    from app.domain.validation import FOLLOW_UP_WORD_TARGETS

    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    ctx = fake_drafter.calls[0]
    prompt = build_draft_prompt(ctx)
    assert str(FOLLOW_UP_WORD_TARGETS[1]) in prompt


def test_a_first_email_prompt_has_no_follow_up_section(repos, ready_founder):
    from app.adapters.gemini import build_draft_prompt

    fake_drafter = FakeDrafter([{
        "subject": "Backend intern at Vahan",
        "body": ("You're matching blue-collar workers to jobs over voice at Vahan. "
                 "That routing problem is what I keep thinking about.\n\n"
                 "I built a scheduler in Python.\n\n"
                 "Would you take an intern?"),
        "research_source_type": "news", "research_source_id": 1,
    }])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    prompt = build_draft_prompt(fake_drafter.calls[0])
    assert "THIS IS A FOLLOW-UP" not in prompt
    assert "THREAD SO FAR" not in prompt


# --- Fix round 1: coordinator-flagged defects and mutation pins ------------


def test_the_prompt_labels_the_sender_and_founder_correctly_not_swapped():
    # Review defect 1: swapping "you sent" / "they replied" in
    # `_format_thread_history` left every existing test green. Content-
    # anchored: find the line each marker body actually landed on, and
    # check ITS label, so an inversion of the labels fails this directly.
    from app.adapters.gemini import build_draft_prompt

    ctx = {
        "company": {"name": "Vahan", "one_liner": "Recruitment over voice"},
        "founder": {"full_name": "Madhav Krishna", "title": "Founder", "bio": None},
        "available_sources": [], "claims": [],
        "sequence_step": 1,
        "thread_history": [
            {"direction": "outbound", "sequence_step": 0, "subject": "s0",
             "body": "OUTBOUND_MARKER_FROM_THE_SENDER", "label": None},
            {"direction": "inbound", "sequence_step": 0, "subject": None,
             "body": "INBOUND_MARKER_FROM_THE_FOUNDER", "label": None},
        ],
    }
    prompt = build_draft_prompt(ctx)
    sender_line = next(l for l in prompt.splitlines() if "OUTBOUND_MARKER_FROM_THE_SENDER" in l)
    founder_line = next(l for l in prompt.splitlines() if "INBOUND_MARKER_FROM_THE_FOUNDER" in l)
    assert "you sent" in sender_line
    assert "they replied" in founder_line


def test_an_out_of_office_inbound_row_carries_its_label_into_thread_history(repos, ready_follow_up):
    # Review defect 3 / ruling T12-2: thread_history must carry `label` so
    # the prompt can tell a bot's auto-reply apart from the founder.
    from datetime import datetime, timezone

    repos.message.upsert(
        outreach_id=ready_follow_up.outreach.id, sequence_step=0, direction="inbound",
        body="I'm out of office until the 20th.", label="OUT_OF_OFFICE",
        received_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    (inbound_entry,) = [e for e in fake_drafter.calls[0]["thread_history"] if e["direction"] == "inbound"]
    assert inbound_entry["label"] == "OUT_OF_OFFICE"


def test_the_prompt_marks_an_out_of_office_reply_as_not_the_founder():
    # Review defect 3: the prompt's own instruction ("acknowledge their
    # reply") must not fire on the only kind of inbound row that can
    # actually reach a follow-up draft (a machine label).
    from app.adapters.gemini import build_draft_prompt

    ctx = {
        "company": {"name": "Vahan", "one_liner": "Recruitment over voice"},
        "founder": {"full_name": "Madhav Krishna", "title": "Founder", "bio": None},
        "available_sources": [], "claims": [],
        "sequence_step": 1,
        "thread_history": [
            {"direction": "outbound", "sequence_step": 0, "subject": "s0", "body": "b0", "label": None},
            {"direction": "inbound", "sequence_step": 0, "subject": None,
             "body": "Out of office until the 20th", "label": "OUT_OF_OFFICE"},
        ],
    }
    prompt = build_draft_prompt(ctx)
    reply_line = next(l for l in prompt.splitlines() if "Out of office until the 20th" in l)
    assert "automated reply" in reply_line
    assert "OUT_OF_OFFICE" in reply_line
    assert "they replied" not in reply_line


def test_a_first_email_has_empty_thread_history_even_with_a_stray_inbound_row(repos, ready_founder):
    # Mutation pin (survivor 3): the `if step <= 0: return []` guard in
    # `_thread_history` is otherwise unexercised, since the inbound filter
    # doesn't look at `step` at all — without the guard, a stray inbound row
    # already sitting on a step-0 outreach would leak into a "first email"'s
    # thread_history purely by accident.
    from datetime import datetime, timezone

    repos.message.upsert(
        outreach_id=ready_founder.id, sequence_step=0, direction="inbound",
        body="stray reply that predates any real send",
        received_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    fake_drafter = FakeDrafter([{
        "subject": "Backend intern at Vahan",
        "body": ("You're matching blue-collar workers to jobs over voice at Vahan. "
                 "That routing problem is what I keep thinking about.\n\n"
                 "I built a scheduler in Python.\n\n"
                 "Would you take an intern?"),
        "research_source_type": "news", "research_source_id": 1,
    }])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    assert fake_drafter.calls[0]["thread_history"] == []


def test_a_redraft_of_the_same_step_does_not_quote_its_own_existing_draft_back_at_itself(
    repos, ready_follow_up,
):
    # Mutation pin (survivor 4): `sequence_step < step` vs `<= step` in the
    # outbound filter. Simulates the legal `PENDING_APPROVAL -> READY_TO_
    # DRAFT` redraft path: a step-1 outbound row already exists (a rejected
    # attempt, or one a reviewer sent back) when DraftOutreach runs again
    # for that same step — it must never show up as "history" for itself.
    repos.message.upsert(
        outreach_id=ready_follow_up.outreach.id, sequence_step=1, direction="outbound",
        subject="old attempt", body="an earlier rejected attempt at the follow-up",
    )
    fake_drafter = FakeDrafter([GOOD_FOLLOW_UP])
    DraftOutreach(fake_drafter, repos).run(limit=1)
    steps_shown = [e["sequence_step"] for e in fake_drafter.calls[0]["thread_history"]]
    assert steps_shown == [0]
