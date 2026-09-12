"""Task 16: the terminal review loop (spec D5/§16, ruling R2).

The two tests under "brief" are the task brief's, verbatim. The rest pin
what the brief's two would let a mutant slip past: the screen shows the
COMPOSED wire text (greeting + body + signature + opt-out, ruling R2), not
the bare body; a needs-manual-write flag is visible; `--dry-run` never
prompts; and every key of the loop dispatches to the use case and commits
(or, for skip/quit/a refused approve/a failed edit, does not).

The loop is driven through Typer's `CliRunner` with `_session`,
`_review_repos`, `_read_key` and `_edit_text` monkeypatched, so the real
`ReviewOutreach` runs against the in-memory `FakeRepos` bundle - no DB, no
terminal, no `$EDITOR`.
"""

import pytest
from typer.testing import CliRunner

import app.cli as cli
from app.cli import render_review_screen
from app.config import get_settings
from app.domain.compose import OPT_OUT_LINE, compose_wire_text
from app.domain.states import OutreachState as S
from tests.fakes_repos import PENDING_BODY, PENDING_SUBJECT, FakeCompanyRow, FakeFounderRow

runner = CliRunner()

# --- brief -------------------------------------------------------------------


def test_review_screen_shows_the_cited_source(a_pending_message, a_news_source):
    out = render_review_screen(a_pending_message, a_news_source)
    assert a_news_source.title[:20] in out
    assert "SOURCE" in out


def test_review_screen_shows_word_count(a_pending_message, a_news_source):
    out = render_review_screen(a_pending_message, a_news_source)
    assert "words" in out.lower()


# --- rendering ---------------------------------------------------------------

MADHAV = FakeFounderRow(id=5, company_id=5, full_name="Madhav Krishna", title="Founder & CEO")
VAHAN = FakeCompanyRow(id=5, name="Vahan", one_liner="Recruitment over voice")


def _screen(message, source, **kw):
    kw.setdefault("company", VAHAN)
    kw.setdefault("founder", MADHAV)
    kw.setdefault("signature", "Suryansh\nIIT Delhi")
    return render_review_screen(message, source, **kw)


def test_screen_shows_the_composed_wire_text_not_the_bare_body(a_pending_message, a_news_source):
    """Ruling R2: what the human approves is byte-for-byte what is sent."""
    out = _screen(a_pending_message, a_news_source)
    expected = compose_wire_text("Madhav", PENDING_BODY, "Suryansh\nIIT Delhi")
    assert expected in out
    assert "Hi Madhav," in out
    assert "Suryansh\nIIT Delhi" in out
    assert OPT_OUT_LINE in out
    # The greeting precedes the body and the signature follows it.
    assert out.index("Hi Madhav,") < out.index(PENDING_BODY) < out.index(OPT_OUT_LINE)


def test_screen_uses_the_signature_it_is_given(a_pending_message, a_news_source):
    out = _screen(a_pending_message, a_news_source, signature="Someone Else")
    assert "Someone Else" in out and "IIT Delhi" not in out


def test_screen_uses_an_explicit_wire_text_when_given(a_pending_message, a_news_source):
    out = _screen(a_pending_message, a_news_source, wire_text="PRECOMPOSED TEXT")
    assert "PRECOMPOSED TEXT" in out and "Hi Madhav," not in out


def test_screen_shows_subject_company_founder_and_recipient(a_pending_message, a_news_source):
    out = _screen(a_pending_message, a_news_source, to="madhav@vahan.ai")
    assert PENDING_SUBJECT in out
    assert "Vahan" in out and "Recruitment over voice" in out
    assert "Madhav Krishna" in out and "Founder & CEO" in out
    assert "madhav@vahan.ai" in out


def test_screen_word_count_is_the_body_word_count(a_pending_message, a_news_source):
    out = _screen(a_pending_message, a_news_source)
    assert f"{len(PENDING_BODY.split())} words" in out
    # Not the wire text's count (greeting/signature/opt-out would inflate it).
    wire = compose_wire_text("Madhav", PENDING_BODY, "Suryansh\nIIT Delhi")
    assert f"{len(wire.split())} words" not in out


def test_screen_shows_the_source_kind_and_truncates_long_source_text(a_pending_message, a_news_source):
    a_news_source.title = "x" * 1000
    out = _screen(a_pending_message, a_news_source)
    assert "news" in out
    assert "x" * 300 in out and "x" * 320 not in out


def test_screen_flags_a_needs_manual_write_row(a_pending_message, a_news_source):
    out = _screen(a_pending_message, a_news_source, last_error="hook shares no distinctive term")
    assert "NEEDS MANUAL WRITE" in out and "hook shares no distinctive term" in out
    assert "NEEDS MANUAL WRITE" not in _screen(a_pending_message, a_news_source)


def test_screen_handles_a_flagged_row_with_no_draft_at_all(a_news_source):
    out = _screen(None, None, last_error="needs manual write: no research material")
    assert "NEEDS MANUAL WRITE" in out and "no draft" in out.lower()
    assert "SOURCE" in out and "none" in out.lower()


def test_screen_shows_the_counter_and_the_key_legend(a_pending_message, a_news_source):
    out = _screen(a_pending_message, a_news_source, index=2, total=7)
    assert "2 of 7" in out
    assert "[a]pprove [e]dit [d]raft again [r]eject [s]kip [q]uit" in out   # T16-1 legend


def test_screen_greets_by_first_name_falling_back_to_there(a_pending_message, a_news_source):
    assert "Hi there," in _screen(a_pending_message, a_news_source, founder=None)


# --- editor round trip -------------------------------------------------------


def test_editor_text_is_subject_blank_line_body_and_splits_back():
    text = cli._editor_text(PENDING_SUBJECT, PENDING_BODY)
    assert text == f"{PENDING_SUBJECT}\n\n{PENDING_BODY}"
    assert cli._split_edit(text) == (PENDING_SUBJECT, PENDING_BODY)


def test_split_edit_tolerates_a_missing_blank_line_and_surrounding_whitespace():
    assert cli._split_edit("  Subj  \nline one\nline two\n\n") == ("Subj", "line one\nline two")
    assert cli._split_edit("Subj") == ("Subj", "")
    assert cli._split_edit("") == ("", "")


# --- the loop ----------------------------------------------------------------


class _Ctx:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


@pytest.fixture
def wired_review(monkeypatch, repos):
    """The `review` command against `FakeRepos`: keys come from `keys`,
    the editor from `editor` (a callable set on the fixture), and the
    session commits are counted on `repos.session`."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("SIGNATURE", "Suryansh")
    get_settings.cache_clear()

    class _Wiring:
        keys: list[str] = []
        prompts = 0
        editor_calls: list[str] = []
        editor = staticmethod(lambda text: None)

    def read_key() -> str:
        _Wiring.prompts += 1
        assert _Wiring.keys, "the loop prompted more times than the test scripted"
        return _Wiring.keys.pop(0)

    def edit_text(text: str):
        _Wiring.editor_calls.append(text)
        return _Wiring.editor(text)

    monkeypatch.setattr(cli, "_session", lambda: _Ctx(repos.session))
    monkeypatch.setattr(cli, "_review_repos", lambda session: repos)
    monkeypatch.setattr(cli, "_read_key", read_key)
    monkeypatch.setattr(cli, "_edit_text", edit_text)
    yield _Wiring
    get_settings.cache_clear()


def test_review_approve_sets_approved_at_transitions_and_commits(wired_review, repos, one_pending):
    wired_review.keys = ["a"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert repos.outreach.get(one_pending.id).state == S.APPROVED
    assert repos.message.get_outbound(one_pending.id, 0).approved_at is not None
    assert repos.session.commit_calls == 1
    assert "approved 1" in result.output


def test_review_reject_discards_and_commits(wired_review, repos, one_pending):
    wired_review.keys = ["r"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert repos.outreach.get(one_pending.id).state == S.DISCARDED
    assert repos.message.get_outbound(one_pending.id, 0).approved_at is None
    assert repos.session.commit_calls == 1


def test_review_skip_changes_nothing_and_does_not_commit(wired_review, repos, one_pending):
    wired_review.keys = ["s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert repos.outreach.get(one_pending.id).state == S.PENDING_APPROVAL
    assert repos.session.commit_calls == 0


def test_review_quit_stops_before_the_next_row(wired_review, repos, two_pending):
    first, second = two_pending
    wired_review.keys = ["q"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert wired_review.prompts == 1
    # Both are news-backed with no team_size, so the tie-break is founder
    # name: Asha renders first, and Madhav (second) is never rendered.
    assert "Asha Rao" in result.output and "Madhav Krishna" not in result.output
    assert repos.outreach.get(first.id).state == S.PENDING_APPROVAL
    assert repos.outreach.get(second.id).state == S.PENDING_APPROVAL


def test_review_screen_in_the_loop_shows_the_wire_text_with_the_configured_signature(
    wired_review, repos, one_pending,
):
    wired_review.keys = ["s"]
    result = runner.invoke(cli.app, ["review"])
    assert compose_wire_text("Madhav", PENDING_BODY, "Suryansh") in result.output


def test_review_dry_run_renders_every_row_and_never_prompts(wired_review, repos, two_pending):
    first, second = two_pending
    wired_review.keys = ["a", "a"]                  # would approve both if consulted
    result = runner.invoke(cli.app, ["review", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert wired_review.prompts == 0
    assert "Madhav Krishna" in result.output and "Asha Rao" in result.output
    assert "1 of 2" in result.output and "2 of 2" in result.output
    assert repos.outreach.get(first.id).state == S.PENDING_APPROVAL
    assert repos.outreach.get(second.id).state == S.PENDING_APPROVAL
    assert repos.session.commit_calls == 0


def test_review_with_nothing_pending_says_so(wired_review, repos, one_approved):
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert "nothing" in result.output.lower()
    assert wired_review.prompts == 0


def test_review_unknown_key_reprompts(wired_review, repos, one_pending):
    wired_review.keys = ["x", "?", "s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert wired_review.prompts == 3
    assert repos.outreach.get(one_pending.id).state == S.PENDING_APPROVAL


def test_review_edit_opens_the_editor_on_subject_and_body_then_recomposes(
    wired_review, repos, one_pending,
):
    new_body = PENDING_BODY.replace("Would you take an intern?", "Could you use an intern?")
    wired_review.editor = staticmethod(lambda text: f"Intern at Vahan\n\n{new_body}")
    wired_review.keys = ["e", "a"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert wired_review.editor_calls == [f"{PENDING_SUBJECT}\n\n{PENDING_BODY}"]
    row = repos.message.get_outbound(one_pending.id, 0)
    assert (row.subject, row.body, row.edited_by_user) == ("Intern at Vahan", new_body, True)
    # The preview re-composed from the EDITED body before the approve prompt.
    assert compose_wire_text("Madhav", new_body, "Suryansh") in result.output
    assert repos.outreach.get(one_pending.id).state == S.APPROVED
    assert row.approved_at is not None
    assert repos.session.commit_calls == 2                # one for the edit, one for the approve


def test_review_edit_that_fails_validation_prints_errors_persists_nothing_and_reprompts(
    wired_review, repos, one_pending,
):
    wired_review.editor = staticmethod(
        lambda text: f"{PENDING_SUBJECT}\n\nI hope this email finds you well. Would you take an intern?"
    )
    wired_review.keys = ["e", "s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert "banned phrase" in result.output
    row = repos.message.get_outbound(one_pending.id, 0)
    assert (row.subject, row.body, row.edited_by_user) == (PENDING_SUBJECT, PENDING_BODY, False)
    assert repos.session.commit_calls == 0
    assert wired_review.prompts == 2


def test_review_after_a_failed_edit_the_editor_reopens_on_the_rejected_text(
    wired_review, repos, one_pending,
):
    bad = f"{PENDING_SUBJECT}\n\nI hope this email finds you well. Would you take an intern?"
    wired_review.editor = staticmethod(lambda text: bad)
    wired_review.keys = ["e", "e", "s"]
    runner.invoke(cli.app, ["review"])
    assert wired_review.editor_calls == [f"{PENDING_SUBJECT}\n\n{PENDING_BODY}", bad]


def test_review_edit_aborted_in_the_editor_changes_nothing(wired_review, repos, one_pending):
    wired_review.editor = staticmethod(lambda text: None)   # click.edit returns None when unchanged
    wired_review.keys = ["e", "s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    row = repos.message.get_outbound(one_pending.id, 0)
    assert (row.body, row.edited_by_user) == (PENDING_BODY, False)
    assert repos.session.commit_calls == 0
    # Reported as an abort, not run through the validator as an empty edit.
    assert "aborted" in result.output and "empty" not in result.output


def test_review_refuses_to_approve_an_unedited_needs_manual_write_row(
    wired_review, repos, manual_write_pending,
):
    wired_review.keys = ["a", "s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert "NEEDS MANUAL WRITE" in result.output
    assert "edit" in result.output.lower() and "refus" in result.output.lower()
    assert repos.outreach.get(manual_write_pending.id).state == S.PENDING_APPROVAL
    assert repos.message.get_outbound(manual_write_pending.id, 0).approved_at is None
    assert repos.session.commit_calls == 0
    assert wired_review.prompts == 2


def test_review_edit_then_approve_of_a_needs_manual_write_row(
    wired_review, repos, manual_write_pending,
):
    wired_review.editor = staticmethod(lambda text: f"{PENDING_SUBJECT}\n\n{PENDING_BODY}")
    wired_review.keys = ["e", "a"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    o = repos.outreach.get(manual_write_pending.id)
    assert o.state == S.APPROVED and o.last_error is None
    assert repos.message.get_outbound(manual_write_pending.id, 0).approved_at is not None


def test_review_edit_writes_the_first_draft_for_a_row_with_no_research_material(
    wired_review, repos, no_material_pending,
):
    body = "You're building Vahan.\n\nI built a scheduler in Python.\n\nWould you take an intern?"
    wired_review.editor = staticmethod(lambda text: f"Intern at Vahan\n\n{body}")
    wired_review.keys = ["e", "a"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert wired_review.editor_calls == ["\n\n"]          # nothing to pre-fill
    row = repos.message.get_outbound(no_material_pending.id, 0)
    assert row is not None and row.body == body and row.edited_by_user
    assert row.research_source_type is None
    assert repos.outreach.get(no_material_pending.id).state == S.APPROVED
    # The row was reloaded after the edit: the preview shown before the
    # approve prompt is the new draft, not "(no draft yet)".
    assert compose_wire_text("Vikram", body, "Suryansh") in result.output
    assert result.output.count("(no draft yet") == 1


def test_review_shows_the_flag_line_for_a_needs_manual_write_row(wired_review, repos, manual_write_pending):
    result = runner.invoke(cli.app, ["review", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "NEEDS MANUAL WRITE: contains a banned phrase" in result.output


def test_review_warns_when_no_signature_is_configured(monkeypatch, wired_review, repos, one_pending):
    monkeypatch.setenv("SIGNATURE", "   ")
    get_settings.cache_clear()
    wired_review.keys = ["s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert "warning" in result.output.lower() and "SIGNATURE" in result.output
    assert compose_wire_text("Madhav", PENDING_BODY, "") in result.output


def test_review_does_not_warn_when_a_signature_is_configured(wired_review, repos, one_pending):
    wired_review.keys = ["s"]
    result = runner.invoke(cli.app, ["review"])
    assert "warning" not in result.output.lower()


def test_review_summary_counts_each_action(wired_review, repos, two_pending):
    wired_review.keys = ["a", "r"]
    result = runner.invoke(cli.app, ["review"])
    assert "approved 1" in result.output and "rejected 1" in result.output


# --- Fix round 1 ---------------------------------------------------------------


def test_review_draft_again_sends_the_row_back_and_commits(wired_review, repos, one_pending):
    """T16-1: `[d]` is `PENDING_APPROVAL -> READY_TO_DRAFT` and nothing else -
    the row, its citation and its approval stay exactly as they were for
    `DraftOutreach._finish` to replace in place on the next `draft` run."""
    wired_review.keys = ["d"]
    before = repos.message.get_outbound(one_pending.id, 0)
    snapshot = (before.id, before.subject, before.body, before.research_source_type,
                before.research_source_id, before.approved_at, before.edited_by_user)
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert repos.outreach.get(one_pending.id).state == S.READY_TO_DRAFT
    after = repos.message.get_outbound(one_pending.id, 0)
    assert after is before and (after.id, after.subject, after.body, after.research_source_type,
                                after.research_source_id, after.approved_at, after.edited_by_user) == snapshot
    assert repos.session.commit_calls == 1
    assert "redrafted 1" in result.output and "READY_TO_DRAFT" in result.output


def test_review_draft_again_works_for_a_row_flagged_for_its_citation(wired_review, repos, manual_write_pending):
    wired_review.keys = ["d"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    o = repos.outreach.get(manual_write_pending.id)
    assert o.state == S.READY_TO_DRAFT
    assert o.last_error == "contains a banned phrase: 'i hope this email finds you well'"  # cleared by the redraft, not here


def test_review_draft_again_is_in_the_legend_of_the_rendered_screen(wired_review, repos, one_pending):
    wired_review.keys = ["s"]
    result = runner.invoke(cli.app, ["review"])
    assert "[d]raft again" in result.output


UNVERIFIED_BODY = PENDING_BODY.replace("I built a scheduler in Python.", "I wrote the ingest layer in Rust.")


def _mark_every_claim_unverified(repos):
    profile = repos.resume_profile.profile
    profile.claims = [{**c, "span_verified": False} for c in profile.claims]   # fresh dicts, no shared state


def _add_unverified_rust_claim(repos):
    repos.resume_profile.profile.claims.append(
        {"kind": "skill", "text": "wrote an ingest layer in Rust", "source_span": "not in the resume",
         "span_verified": False},
    )


def test_review_edit_citing_an_unverified_claim_is_rejected_without_keep_unverified(
    wired_review, repos, one_pending,
):
    """F1 / T16-3: mirrors `draft --keep-unverified` (R3) so an edit is held
    to the same claim set the drafter was."""
    _add_unverified_rust_claim(repos)
    wired_review.editor = staticmethod(lambda text: f"{PENDING_SUBJECT}\n\n{UNVERIFIED_BODY}")
    wired_review.keys = ["e", "s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert "no resume claim" in result.output and "Rust" in result.output
    assert repos.message.get_outbound(one_pending.id, 0).body == PENDING_BODY
    assert repos.session.commit_calls == 0


def test_review_edit_citing_an_unverified_claim_is_accepted_with_keep_unverified(
    wired_review, repos, one_pending,
):
    _add_unverified_rust_claim(repos)
    wired_review.editor = staticmethod(lambda text: f"{PENDING_SUBJECT}\n\n{UNVERIFIED_BODY}")
    wired_review.keys = ["e", "s"]
    result = runner.invoke(cli.app, ["review", "--keep-unverified"])
    assert result.exit_code == 0, result.output
    assert "no resume claim" not in result.output
    row = repos.message.get_outbound(one_pending.id, 0)
    assert row.body == UNVERIFIED_BODY and row.edited_by_user
    assert repos.session.commit_calls == 1


def test_review_edit_is_possible_at_all_when_every_claim_is_unverified_only_with_the_flag(
    wired_review, repos, one_pending,
):
    """The reviewer's reproduction (a): resume with only unverified claims,
    drafted under `--keep-unverified`; without the flag every edit raises
    the 'run with --keep-unverified' error and nothing can be edited."""
    _mark_every_claim_unverified(repos)
    wired_review.editor = staticmethod(lambda text: f"{PENDING_SUBJECT}\n\n{PENDING_BODY}")
    wired_review.keys = ["e", "s"]
    result = runner.invoke(cli.app, ["review"])
    assert "--keep-unverified" in result.output
    assert repos.message.get_outbound(one_pending.id, 0).edited_by_user is False

    wired_review.keys = ["e", "s"]
    result = runner.invoke(cli.app, ["review", "--keep-unverified"])
    assert result.exit_code == 0, result.output
    assert repos.message.get_outbound(one_pending.id, 0).edited_by_user is True


def test_review_after_an_edit_that_raises_the_editor_reopens_on_the_typed_text(
    wired_review, repos, one_pending,
):
    """F8: `use_case.edit` *raising* (not returning errors) used to drop the
    operator's text - the next `[e]` reopened on the pre-edit draft."""
    _mark_every_claim_unverified(repos)                # makes `edit` raise ValueError
    typed = f"{PENDING_SUBJECT}\n\nMy carefully typed text."
    wired_review.editor = staticmethod(lambda text: typed)
    wired_review.keys = ["e", "e", "s"]
    result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == 0, result.output
    assert wired_review.editor_calls == [f"{PENDING_SUBJECT}\n\n{PENDING_BODY}", typed]
    assert repos.session.commit_calls == 0
