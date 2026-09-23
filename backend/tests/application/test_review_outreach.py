"""Task 16: `ReviewOutreach` - approve / edit / reject / skip over
`PENDING_APPROVAL` rows (spec D5, §6 state machine, §13 "approval cannot be
bypassed", ruling R2).

Every state change goes through `assert_transition`; `approve` stamps
`approved_at` on the step's outbound row (the DB CHECK `sent_requires_
approved` and `reserve_for_send`'s `approved_at IS NOT NULL` both key off
it); `edit` re-runs `validate_draft` and persists nothing on failure; a row
flagged needs-manual-write CAN be approved (2026-09-22 - the checks are
advice, and the operator signs the letter), and keeps its note.
"""

from datetime import datetime, timezone

import pytest

from app.application.review_outreach import ReviewOutreach
from app.domain.states import IllegalTransition
from app.domain.states import OutreachState as S
from tests.fakes_repos import (
    MANUAL_WRITE_ERROR,
    PENDING_BODY,
    PENDING_SUBJECT,
    VAHAN_NEWS,
    _add_pending_founder,
)

NOW = datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc)


# --- listing -----------------------------------------------------------------


def test_pending_lists_only_pending_approval_rows_with_their_context(repos, one_pending, one_approved):
    items = ReviewOutreach(repos).pending()
    assert [i.founder.id for i in items] == [one_pending.id]
    item = items[0]
    assert item.outreach.state == S.PENDING_APPROVAL
    assert item.company.name == "Vahan"
    assert item.message.body == PENDING_BODY
    assert item.source is not None
    assert item.source.kind == "news" and item.source.text == VAHAN_NEWS
    assert item.source.publication == "The Economic Times"


def test_pending_item_for_a_row_without_a_draft_has_no_message_and_no_source(repos, no_material_pending):
    (item,) = ReviewOutreach(repos).pending()
    assert item.message is None and item.source is None
    assert item.outreach.last_error.startswith("needs manual write")


def test_pending_orders_news_backed_companies_before_the_rest(repos):
    """Spec §8 `rank_key`: strength of research material first (news >
    launch > rest), then team_size ascending, then founder name."""
    # Names and team sizes are chosen so that sorting by EITHER alone gives
    # a different order from the spec's (strength, team_size, name).
    desc_only = _add_pending_founder(repos, 40, "Aaron Desc", "z@example.com")
    repos.news.rows = [n for n in repos.news.rows if n.company_id != 40]
    repos.company.get(40).team_size = 1
    launch_backed = _add_pending_founder(repos, 41, "Abe Launch", "y@example.com")
    repos.news.rows = [n for n in repos.news.rows if n.company_id != 41]
    repos.launch.add(company_id=41, title="Launch", body="Launching")
    repos.company.get(41).team_size = 2
    news_big = _add_pending_founder(repos, 42, "Al News", "b@example.com")
    repos.company.get(42).team_size = 50
    news_small = _add_pending_founder(repos, 43, "Bea News", "a@example.com")
    repos.company.get(43).team_size = 5
    items = ReviewOutreach(repos).pending()
    assert [i.founder.id for i in items] == [news_small.id, news_big.id, launch_backed.id, desc_only.id]


def test_pending_ties_on_strength_and_team_size_break_on_founder_name(repos):
    b = _add_pending_founder(repos, 44, "Bea Tie", "b@example.com")
    a = _add_pending_founder(repos, 45, "Al Tie", "a@example.com")
    assert [i.founder.id for i in ReviewOutreach(repos).pending()] == [a.id, b.id]


def test_load_returns_the_current_row_for_one_outreach(repos, one_pending):
    item = ReviewOutreach(repos).load(one_pending.id)
    assert item.founder.id == one_pending.id and item.message.subject == PENDING_SUBJECT


def test_load_of_an_unknown_outreach_raises(repos):
    with pytest.raises(ValueError):
        ReviewOutreach(repos).load(999)


# --- approve -----------------------------------------------------------------


def test_approve_stamps_approved_at_with_now_and_moves_to_approved(repos, one_pending):
    ReviewOutreach(repos).approve(one_pending.id, NOW)
    assert repos.outreach.get(one_pending.id).state == S.APPROVED
    assert repos.message.get_outbound(one_pending.id, 0).approved_at == NOW


def test_approve_from_a_non_pending_state_is_an_illegal_transition(repos, one_approved):
    with pytest.raises(IllegalTransition):
        ReviewOutreach(repos).approve(one_approved.id, NOW)


def test_an_unedited_needs_manual_write_row_can_still_be_approved(repos, manual_write_pending):
    # 2026-09-22: this used to be a refusal. The operator's ruling: the
    # checks are advice, and the person pressing approve is the one whose
    # name is on the letter. The note is still stored and still shown; it
    # no longer overrules him.
    ReviewOutreach(repos).approve(manual_write_pending.id, NOW)
    o = repos.outreach.get(manual_write_pending.id)
    assert o.state == S.APPROVED
    # The note survives the approve - it is a record of what was noticed,
    # not a lock that got picked.
    assert o.last_error == MANUAL_WRITE_ERROR
    assert repos.message.get_outbound(manual_write_pending.id, 0).approved_at == NOW


def test_approve_is_refused_when_there_is_no_draft_row_at_all(repos, no_material_pending):
    with pytest.raises(ValueError, match="edit"):
        ReviewOutreach(repos).approve(no_material_pending.id, NOW)
    assert repos.outreach.get(no_material_pending.id).state == S.PENDING_APPROVAL


def test_approve_succeeds_once_a_needs_manual_write_row_has_been_edited(repos, manual_write_pending):
    uc = ReviewOutreach(repos)
    assert uc.edit(manual_write_pending.id, PENDING_SUBJECT, PENDING_BODY) == []
    uc.approve(manual_write_pending.id, NOW)
    o = repos.outreach.get(manual_write_pending.id)
    assert o.state == S.APPROVED and o.last_error is None
    assert repos.message.get_outbound(manual_write_pending.id, 0).approved_at == NOW


def test_approve_accepts_a_flagged_row_whose_body_was_edited(repos, manual_write_pending):
    """The guard is "flagged AND unedited": a flag left on an already
    edited row (edited_by_user=True) must not block the human's own text."""
    row = repos.message.get_outbound(manual_write_pending.id, 0)
    row.body, row.edited_by_user = PENDING_BODY, True
    ReviewOutreach(repos).approve(manual_write_pending.id, NOW)
    assert repos.outreach.get(manual_write_pending.id).state == S.APPROVED
    assert row.approved_at == NOW


def test_approve_does_not_touch_the_stored_body(repos, one_pending):
    ReviewOutreach(repos).approve(one_pending.id, NOW)
    row = repos.message.get_outbound(one_pending.id, 0)
    assert row.body == PENDING_BODY and row.edited_by_user is False


# --- reject ------------------------------------------------------------------


def test_reject_moves_to_discarded_and_leaves_the_draft_unapproved(repos, one_pending):
    ReviewOutreach(repos).reject(one_pending.id)
    assert repos.outreach.get(one_pending.id).state == S.DISCARDED
    assert repos.message.get_outbound(one_pending.id, 0).approved_at is None


def test_reject_from_a_non_pending_state_is_an_illegal_transition(repos, one_approved):
    with pytest.raises(IllegalTransition):
        ReviewOutreach(repos).reject(one_approved.id)
    assert repos.outreach.get(one_approved.id).state == S.APPROVED


def test_reject_of_a_flagged_row_without_a_draft_works(repos, no_material_pending):
    ReviewOutreach(repos).reject(no_material_pending.id)
    assert repos.outreach.get(no_material_pending.id).state == S.DISCARDED


# --- edit --------------------------------------------------------------------

BANNED_BODY = "I hope this email finds you well. Would you take an intern?"

# 2026-09-22: the phrase bans no longer apply to an operator's own edit
# (they police the model's habits, not his word choices), so an edit test
# that wants a *failure* has to break a structural rule instead. Implying
# seniority is one the operator is still held to - it is a false claim
# about him, not a matter of taste.
SENIORITY_BODY = (
    "You're matching blue-collar workers to jobs over voice at Vahan. "
    "That routing problem is what I keep thinking about.\n\n"
    "I built a scheduler in Python with my team.\n\n"
    "Would you take an intern?"
)


def test_an_edit_the_checks_dislike_is_still_saved(repos, one_pending):
    # 2026-09-22: the checks no longer refuse a save. They are advice
    # about the operator's own words - the same ruling that stopped
    # `approve` refusing. The text is kept; the notes come back.
    notes = ReviewOutreach(repos).edit(one_pending.id, "New subject", SENIORITY_BODY)
    assert notes and any("implies seniority" in n for n in notes)
    row = repos.message.get_outbound(one_pending.id, 0)
    assert (row.subject, row.body, row.edited_by_user) == ("New subject", SENIORITY_BODY, True)
    assert repos.outreach.get(one_pending.id).state == S.PENDING_APPROVAL


def test_edit_that_validates_persists_and_marks_edited_by_user(repos, one_pending):
    new_body = PENDING_BODY.replace("Would you take an intern?", "Could you use an intern?")
    assert ReviewOutreach(repos).edit(one_pending.id, "  Intern at Vahan ", new_body + "\n") == []
    row = repos.message.get_outbound(one_pending.id, 0)
    assert (row.subject, row.body, row.edited_by_user) == ("Intern at Vahan", new_body, True)
    assert row.approved_at is None                       # editing never approves
    assert repos.outreach.get(one_pending.id).state == S.PENDING_APPROVAL


def test_edit_clears_a_stale_approved_at(repos, one_pending):
    """An edit is never an approval: a `PENDING_APPROVAL` row that somehow
    carries `approved_at` (data inconsistency) loses it on edit, so the
    send-side `approved_at IS NOT NULL` predicate can't be satisfied by
    text the human never approved."""
    row = repos.message.get_outbound(one_pending.id, 0)
    row.approved_at = NOW
    assert ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, PENDING_BODY) == []
    assert row.approved_at is None


def test_edit_clears_the_needs_manual_write_flag(repos, manual_write_pending):
    ReviewOutreach(repos).edit(manual_write_pending.id, PENDING_SUBJECT, PENDING_BODY)
    assert repos.outreach.get(manual_write_pending.id).last_error is None


def test_an_edit_clears_the_draft_time_flag_even_when_the_checks_complain(repos, manual_write_pending):
    """`last_error` describes what the MODEL wrote. Once the operator has
    rewritten the text it no longer describes anything, so it goes - and
    what the checks make of the new text comes back instead."""
    notes = ReviewOutreach(repos).edit(manual_write_pending.id, PENDING_SUBJECT, BANNED_BODY)
    assert notes
    assert repos.outreach.get(manual_write_pending.id).last_error is None


def test_edit_revalidates_against_the_cited_source(repos, one_pending):
    """A body that no longer shares a term with the cited news item fails
    grounding - the citation stays what the drafter chose."""
    ungrounded = ("Your company Vahan is interesting to me.\n\n"
                  "I built a scheduler in Python.\n\nWould you take an intern?")
    notes = ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, ungrounded)
    assert any("hook shares no distinctive term" in n for n in notes)
    # Saved anyway (2026-09-22): the note is advice, not a refusal.
    assert repos.message.get_outbound(one_pending.id, 0).body == ungrounded


def test_edit_applies_the_resume_claims_rule(repos, one_pending):
    body = PENDING_BODY.replace("I built a scheduler in Python.", "I built a scheduler in Rust.")
    errors = ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, body)
    assert any("no resume claim" in e for e in errors)


@pytest.mark.parametrize("subject, body", [("", PENDING_BODY), ("   ", PENDING_BODY),
                                           (PENDING_SUBJECT, ""), (PENDING_SUBJECT, "  \n ")])
def test_edit_rejects_an_empty_subject_or_body(repos, one_pending, subject, body):
    errors = ReviewOutreach(repos).edit(one_pending.id, subject, body)
    assert errors and any("empty" in e for e in errors)
    row = repos.message.get_outbound(one_pending.id, 0)
    assert (row.subject, row.body) == (PENDING_SUBJECT, PENDING_BODY)


def test_edit_creates_the_draft_row_for_a_flagged_row_without_one(repos, no_material_pending):
    body = "You're building Vahan.\n\nI built a scheduler in Python.\n\nWould you take an intern?"
    assert ReviewOutreach(repos).edit(no_material_pending.id, "Intern at Vahan", body) == []
    row = repos.message.get_outbound(no_material_pending.id, 0)
    assert row is not None
    assert (row.direction, row.sequence_step, row.body, row.edited_by_user) == ("outbound", 0, body, True)
    assert row.research_source_type is None and row.research_source_id is None
    assert row.approved_at is None
    assert repos.outreach.get(no_material_pending.id).last_error is None


def test_edit_without_a_source_still_applies_style_and_claims_rules(repos, no_material_pending):
    notes = ReviewOutreach(repos).edit(no_material_pending.id, "Subject", SENIORITY_BODY)
    assert any("implies seniority" in n for n in notes)
    # The rules still RUN on a row with no citable source (2026-09-22) -
    # they just no longer refuse the save.
    assert repos.message.get_outbound(no_material_pending.id, 0).body == SENIORITY_BODY
    notes = ReviewOutreach(repos).edit(no_material_pending.id, "Subject",
                                       "You're building Vahan.\n\nI built it in Rust.\n\nIntern?")
    assert any("no resume claim" in n for n in notes)


def test_edit_is_refused_in_a_state_that_has_no_draft_to_change(repos, one_approved):
    repos.outreach.get(one_approved.id).state = S.SENT
    with pytest.raises(ValueError, match="PENDING_APPROVAL or APPROVED"):
        ReviewOutreach(repos).edit(one_approved.id, PENDING_SUBJECT, PENDING_BODY, now=NOW)


def test_an_approved_letter_can_be_edited_and_keeps_its_approval(repos, one_approved):
    """Operator request (2026-09-22): waiting to send is exactly when a
    typo gets noticed, and bouncing the letter back through the review
    queue to fix one word is busywork. The edit IS the approval of the new
    text, so the stamp moves to it."""
    new_body = PENDING_BODY.replace("Would you take an intern?", "Could you use an intern?")
    assert ReviewOutreach(repos).edit(one_approved.id, PENDING_SUBJECT, new_body, now=NOW) == []
    row = repos.message.get_outbound(one_approved.id, 0)
    assert (row.body, row.edited_by_user) == (new_body, True)
    # Still approved, and approved AT THE EDIT - the old stamp approved
    # words that no longer exist.
    assert repos.outreach.get(one_approved.id).state == S.APPROVED
    assert row.approved_at == NOW


def test_editing_an_approved_letter_without_a_clock_is_refused(repos, one_approved):
    """There is no sensible third behaviour: keeping the old stamp would
    send text nobody approved, and clearing it would strand the row in
    APPROVED with nothing `reserve_for_send` can act on."""
    with pytest.raises(ValueError, match="clock"):
        ReviewOutreach(repos).edit(one_approved.id, PENDING_SUBJECT, PENDING_BODY)
    assert repos.message.get_outbound(one_approved.id, 0).edited_by_user is False


def test_an_approved_letter_the_checks_dislike_is_still_saved_and_still_approved(repos, one_approved):
    # 2026-09-22: the checks no longer refuse a save. They are advice
    # about the operator's own words - the same ruling that stopped
    # `approve` refusing. The text is kept; the notes come back.
    assert ReviewOutreach(repos).edit(one_approved.id, PENDING_SUBJECT, SENIORITY_BODY, now=NOW)
    row = repos.message.get_outbound(one_approved.id, 0)
    assert row.body == SENIORITY_BODY
    # Re-stamped onto the new text, not left approving the old words.
    assert row.approved_at == NOW
    assert repos.outreach.get(one_approved.id).state == S.APPROVED


def test_edit_refuses_without_resume_claims(repos, one_pending):
    # 2026-09-23: the message now names the button rather than the CLI
    # command, and is an `OperatorError` so the UI may show it verbatim.
    from app.application.errors import OperatorError

    repos.resume_profile.profile = None
    with pytest.raises(OperatorError, match="Re-read my resume"):
        ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, PENDING_BODY)


# --- skip --------------------------------------------------------------------


def test_skip_is_a_no_op(repos, one_pending):
    ReviewOutreach(repos).skip(one_pending.id)
    o = repos.outreach.get(one_pending.id)
    row = repos.message.get_outbound(one_pending.id, 0)
    assert o.state == S.PENDING_APPROVAL and row.approved_at is None and row.edited_by_user is False
    assert repos.session.commit_calls == 0


def test_the_use_case_never_commits_itself(repos, one_pending):
    """The CLI owns the transaction boundary (one commit per action)."""
    uc = ReviewOutreach(repos)
    uc.edit(one_pending.id, PENDING_SUBJECT, PENDING_BODY)
    uc.approve(one_pending.id, NOW)
    assert repos.session.commit_calls == 0


# --- Fix round 1 ---------------------------------------------------------------


def test_redraft_moves_to_ready_to_draft_and_touches_nothing_else(repos, manual_write_pending):
    """T16-1: the row, its citation, its flag and its approval stay as they
    are; `DraftOutreach._finish` replaces the row in place next run."""
    before = repos.message.get_outbound(manual_write_pending.id, 0)
    snapshot = (before.subject, before.body, before.research_source_type, before.research_source_id,
                before.approved_at, before.edited_by_user)
    ReviewOutreach(repos).redraft(manual_write_pending.id)
    o = repos.outreach.get(manual_write_pending.id)
    assert o.state == S.READY_TO_DRAFT
    assert o.last_error == MANUAL_WRITE_ERROR
    after = repos.message.get_outbound(manual_write_pending.id, 0)
    assert after is before
    assert (after.subject, after.body, after.research_source_type, after.research_source_id,
            after.approved_at, after.edited_by_user) == snapshot
    assert repos.session.commit_calls == 0


def test_redraft_from_a_non_pending_state_is_an_illegal_transition(repos, one_approved):
    # APPROVED -> READY_TO_DRAFT is illegal in the state machine AND refused
    # by redraft's own guard (F6); the guard runs first, so it is a ValueError.
    with pytest.raises((ValueError, IllegalTransition)):
        ReviewOutreach(repos).redraft(one_approved.id)
    assert repos.outreach.get(one_approved.id).state == S.APPROVED


@pytest.mark.parametrize("state", [S.NEEDS_EMAIL, S.NEEDS_MANUAL_EMAIL, S.SENT])
def test_redraft_is_refused_from_states_where_ready_to_draft_is_otherwise_legal(repos, one_pending, state):
    """Final fix wave (F6): `READY_TO_DRAFT` is a legal target from these
    three states, so `assert_transition` alone let a use-case caller
    "redraft" a never-drafted or already-sent row. The explicit guard
    refuses everything but PENDING_APPROVAL."""
    repos.outreach.get(one_pending.id).state = str(state)
    with pytest.raises(ValueError, match="PENDING_APPROVAL"):
        ReviewOutreach(repos).redraft(one_pending.id)
    assert repos.outreach.get(one_pending.id).state == str(state)


def test_redraft_then_draft_replaces_the_flagged_row_in_place(repos, manual_write_pending):
    """The round trip the key exists for: a row flagged for its citation
    goes back, and the next draft run updates the SAME row."""
    from app.application.draft_outreach import DraftOutreach
    from tests.fakes import FakeDrafter

    before = repos.message.get_outbound(manual_write_pending.id, 0)
    ReviewOutreach(repos).redraft(manual_write_pending.id)
    DraftOutreach(FakeDrafter([{"subject": PENDING_SUBJECT, "body": PENDING_BODY,
                                "research_source_type": "news",
                                "research_source_id": manual_write_pending.id}]), repos).run()
    o = repos.outreach.get(manual_write_pending.id)
    assert o.state == S.PENDING_APPROVAL and o.last_error is None
    rows = [r for r in repos.message.rows if r.outreach_id == manual_write_pending.id]
    assert len(rows) == 1 and rows[0] is before and rows[0].body == PENDING_BODY


def test_edit_nulls_a_citation_that_no_longer_resolves(repos, one_pending):
    """F3: with an existing row whose citation does not resolve, a valid
    edit must not leave the row claiming a source the text was never
    checked against."""
    row = repos.message.get_outbound(one_pending.id, 0)
    row.research_source_id = 9999
    body = "You're building Vahan.\n\nI built a scheduler in Python.\n\nWould you take an intern?"
    assert ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, body) == []
    assert (row.research_source_type, row.research_source_id) == (None, None)
    assert row.body == body and row.edited_by_user
    assert ReviewOutreach(repos).load(one_pending.id).source is None


def test_edit_keeps_a_citation_that_resolves(repos, one_pending):
    row = repos.message.get_outbound(one_pending.id, 0)
    assert ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, PENDING_BODY) == []
    assert (row.research_source_type, row.research_source_id) == ("news", one_pending.id)


def test_edit_allows_the_founder_name_and_long_description_terms_as_the_drafter_did(repos, one_pending):
    """F6: the F4 allowed-text parity - a capitalised token that is the
    founder's name or a `long_description` term is not a fabrication."""
    repos.company.get(one_pending.company_id).long_description = (
        "Vahan matches blue-collar workers to jobs over voice, screening and placing staff "
        "for large gig platforms such as Swiggy and Zepto across a dozen Indian languages."
    )
    body = ("You're matching blue-collar workers to jobs over voice at Vahan. "
            "That routing problem is what I keep thinking about.\n\n"
            "My scheduler in Python handles a similar queue, and placing staff for Swiggy "
            "is the part Madhav described that I'd like to work on.\n\n"
            "Would you take an intern?")
    assert ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, body) == []


def test_edit_without_the_allowed_text_would_flag_those_same_tokens(repos, one_pending):
    """Guards the test above: the same tokens ARE flagged when they are
    neither the founder's name nor in the description."""
    repos.company.get(one_pending.company_id).long_description = "Too short."
    repos.founder.get(one_pending.id).full_name = "Someone Else"
    body = ("You're matching blue-collar workers to jobs over voice at Vahan. "
            "That routing problem is what I keep thinking about.\n\n"
            "My scheduler in Python handles a similar queue, and placing staff for Swiggy "
            "is the part Madhav described that I'd like to work on.\n\n"
            "Would you take an intern?")
    errors = ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, body)
    assert any("Madhav" in e and "Swiggy" in e for e in errors)


def test_pending_unknown_team_size_sorts_after_known_sizes(repos):
    """F7: `team_size is None` sorts last, mixed with ints without raising."""
    unknown = _add_pending_founder(repos, 46, "Al Unknown", "a@example.com")
    big = _add_pending_founder(repos, 47, "Zed Big", "z@example.com")
    repos.company.get(47).team_size = 10_000
    assert [i.founder.id for i in ReviewOutreach(repos).pending()] == [big.id, unknown.id]


def test_d24_attaching_job_postings_does_not_change_the_review_order(repos):
    """Spec §15 regression (D24): job postings and `isHiring` never affect
    `rank_key`. Rank, attach postings (intern-appropriate and senior) to
    some companies, rank again: byte-identical order."""
    ids = []
    for n, (name, team) in enumerate([("Cal", 30), ("Bea", None), ("Al", 3), ("Dee", 30)], start=50):
        _add_pending_founder(repos, n, f"{name} Founder", f"{name.lower()}@example.com")
        repos.company.get(n).team_size = team
        ids.append(n)
    repos.news.rows = [r for r in repos.news.rows if r.company_id not in (51, 53)]
    repos.launch.add(company_id=53, title="Launch", body="Launching")
    before = [i.founder.id for i in ReviewOutreach(repos).pending()]
    assert before == [52, 50, 53, 51]          # news by team size, then launch, then the rest

    repos.job_posting.add(id=901, company_id=51, title="Software Engineering Intern",
                          employment_type="Internship", min_experience="0 years")
    repos.job_posting.add(id=902, company_id=53, title="Senior Engineer",
                          employment_type="Full-time", min_experience="6+ years")
    repos.job_posting.add(id=903, company_id=52, title="Backend Intern", employment_type="Internship")
    for n in ids:
        repos.company.get(n).is_hiring = True
    after = [i.founder.id for i in ReviewOutreach(repos).pending()]
    assert after == before


# --- Final fix wave (F16): `edit` validates against every news outlet name
# for the company, not only the cited row's.

def test_edit_rejects_a_body_naming_a_non_cited_outlet(repos, one_pending):
    """The cited row is news id == one_pending.id from The Economic Times;
    a second, uncited news row from Mint exists. Naming Mint verbatim in
    the edited body must fail grounding."""
    repos.news.add(id=900, company_id=one_pending.company_id, title="Vahan hires a CFO", source="Mint")
    body = PENDING_BODY.replace("That routing problem is what I keep thinking about.",
                                "Mint called that routing problem the one to watch.")
    notes = ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, body)
    assert any("publication name appears" in n for n in notes)
    assert repos.message.get_outbound(one_pending.id, 0).body == body


def test_an_operator_edit_keeps_his_own_word_choice(repos, one_pending):
    """The phrase bans stop the MODEL reaching for a tell. They are not a
    style review of the person who signs the email: rejecting his edit for
    "genuinely" would be the tool arguing with its owner about his voice.

    Everything structural still applies - the seniority test above edits
    the same fixture and is still rejected.
    """
    body = PENDING_BODY.replace(
        "That routing problem is what I keep thinking about.",
        "That routing problem is something I am passionate about.",
    )
    assert ReviewOutreach(repos).edit(one_pending.id, PENDING_SUBJECT, body) == []
    assert repos.message.get_outbound(one_pending.id, 0).body == body


# --- back to review (operator request 2026-09-22) --------------------------

def test_unapprove_returns_an_approved_letter_to_the_queue(repos, one_approved):
    """Neither "send it" nor "discard it": reject throws the founder away
    and redraft spends a model call. This just undoes the decision."""
    ReviewOutreach(repos).unapprove(one_approved.id)
    assert repos.outreach.get(one_approved.id).state == S.PENDING_APPROVAL
    # The approval goes with it - a PENDING_APPROVAL row still carrying
    # `approved_at` is the inconsistency `send`'s own guard catches.
    assert repos.message.get_outbound(one_approved.id, 0).approved_at is None


def test_an_unapproved_letter_shows_up_in_the_review_queue_again(repos, one_approved):
    uc = ReviewOutreach(repos)
    assert [i.founder.id for i in uc.pending()] == []
    uc.unapprove(one_approved.id)
    assert [i.founder.id for i in uc.pending()] == [one_approved.id]
    assert [i.founder.id for i in uc.approved()] == []


def test_unapprove_keeps_the_text_exactly_as_it_was(repos, one_approved):
    before = repos.message.get_outbound(one_approved.id, 0)
    subject, body = before.subject, before.body
    ReviewOutreach(repos).unapprove(one_approved.id)
    row = repos.message.get_outbound(one_approved.id, 0)
    assert (row.subject, row.body) == (subject, body)


def test_unapprove_is_refused_for_a_letter_that_was_never_approved(repos, one_pending):
    with pytest.raises(ValueError, match="APPROVED"):
        ReviewOutreach(repos).unapprove(one_pending.id)
    assert repos.outreach.get(one_pending.id).state == S.PENDING_APPROVAL


def test_an_unapproved_letter_can_be_approved_again(repos, one_approved):
    uc = ReviewOutreach(repos)
    uc.unapprove(one_approved.id)
    uc.approve(one_approved.id, NOW)
    assert repos.outreach.get(one_approved.id).state == S.APPROVED
    assert repos.message.get_outbound(one_approved.id, 0).approved_at == NOW
