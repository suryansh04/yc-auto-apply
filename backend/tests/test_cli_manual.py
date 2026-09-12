"""Final fix wave (F3, ruling FR-1): the `list-manual` and `set-email` CLI
commands, driven through Typer's `CliRunner` with `_session` and
`_manual_repos` monkeypatched so the real `ResolveManualEmail` runs
against the in-memory `FakeRepos` bundle and commits are counted on
`repos.session`.
"""

import pytest
from typer.testing import CliRunner

import app.cli as cli
from app.cli import render_manual_table
from app.config import get_settings
from app.domain.states import OutreachState as S

runner = CliRunner()


class _Ctx:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


@pytest.fixture
def wired(monkeypatch, repos):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    get_settings.cache_clear()
    monkeypatch.setattr(cli, "_session", lambda: _Ctx(repos.session))
    monkeypatch.setattr(cli, "_manual_repos", lambda session: repos)
    yield repos
    get_settings.cache_clear()


@pytest.fixture
def priya(repos):
    repos.company.add(id=1, domain="acme.com", name="Acme")
    founder = repos.founder.add(id=1, company_id=1, full_name="Priya Nair",
                                linkedin_url="https://linkedin.com/in/priya")
    repos.outreach.add(id=1, founder_id=founder.id, state=str(S.NEEDS_MANUAL_EMAIL))
    repos.email_candidate.upsert(founder_id=1, address="priya@acme.com", pattern="first",
                                 priority=1, verdict="invalid", provider="fake")
    repos.email_candidate.upsert(founder_id=1, address="pnair@acme.com", pattern="f_last",
                                 priority=3, verdict="catch_all", provider="fake")
    repos.email_candidate.upsert(founder_id=1, address="amit@acme.com", pattern=None,
                                 priority=0, verdict="published", provider="fake")
    return founder


# --- list-manual -------------------------------------------------------------


def test_list_manual_shows_id_founder_company_domain_candidates_linkedin_and_hint(wired, priya):
    result = runner.invoke(cli.app, ["list-manual"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Priya Nair" in out and "Acme" in out and "acme.com" in out
    assert "https://linkedin.com/in/priya" in out
    assert "priya@acme.com=invalid" in out and "pnair@acme.com=catch_all" in out
    assert "published hint: amit@acme.com" in out
    assert "set-email ID ADDRESS" in out
    # The outreach id is the first column of the founder's row.
    row = next(line for line in out.splitlines() if "Priya Nair" in line)
    assert row.split()[0] == "1"
    assert wired.session.commit_calls == 0


def test_list_manual_with_nothing_pending_says_so(wired, a_founder):
    result = runner.invoke(cli.app, ["list-manual"])
    assert result.exit_code == 0
    assert "no founders need a manual email" in result.output


def test_render_manual_table_marks_a_founder_with_no_domain(wired):
    wired.company.add(id=2, domain=None, name="Zed")
    f = wired.founder.add(id=2, company_id=2, full_name="Vikram Shah")
    wired.outreach.add(id=2, founder_id=f.id, state=str(S.NEEDS_MANUAL_EMAIL))
    from app.application.resolve_manual_email import ResolveManualEmail

    out = render_manual_table(ResolveManualEmail(wired).pending())
    assert "Vikram Shah" in out and "(none - no domain)" in out and "published hint" not in out


# --- set-email ---------------------------------------------------------------


def test_set_email_stores_the_address_transitions_and_commits(wired, priya):
    result = runner.invoke(cli.app, ["set-email", "1", "Priya.Nair@acme.com"])
    assert result.exit_code == 0, result.output
    o = wired.outreach.get(1)
    assert o.state == S.READY_TO_DRAFT and o.resolved_email == "priya.nair@acme.com"
    assert wired.session.commit_calls == 1
    assert "priya.nair@acme.com" in result.output and "ready_to_draft" in result.output


def test_set_email_rejects_a_malformed_address_and_commits_nothing(wired, priya):
    result = runner.invoke(cli.app, ["set-email", "1", "not-an-address"])
    assert result.exit_code == 1
    assert "does not look like an email address" in result.output
    o = wired.outreach.get(1)
    assert o.state == S.NEEDS_MANUAL_EMAIL and o.resolved_email is None
    assert wired.session.commit_calls == 0


def test_set_email_refuses_a_row_that_is_not_needs_manual_email(wired, a_founder):
    result = runner.invoke(cli.app, ["set-email", str(a_founder.id), "x@onefin.com"])
    assert result.exit_code == 1
    assert "not needs_manual_email" in result.output
    assert wired.outreach.get(a_founder.id).state == S.NEEDS_EMAIL
    assert wired.session.commit_calls == 0


def test_set_email_unresolved_moves_to_unresolved_and_commits(wired, priya):
    result = runner.invoke(cli.app, ["set-email", "1", "--unresolved"])
    assert result.exit_code == 0, result.output
    assert wired.outreach.get(1).state == S.UNRESOLVED
    assert wired.session.commit_calls == 1
    assert "unresolved" in result.output


@pytest.mark.parametrize("args", [["set-email", "1"], ["set-email", "1", "x@acme.com", "--unresolved"]])
def test_set_email_needs_exactly_one_of_address_or_unresolved(wired, priya, args):
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert "exactly one of ADDRESS or --unresolved" in result.output
    assert wired.outreach.get(1).state == S.NEEDS_MANUAL_EMAIL
    assert wired.session.commit_calls == 0


def test_set_email_then_list_manual_no_longer_lists_the_founder(wired, priya):
    runner.invoke(cli.app, ["set-email", "1", "priya@acme.com"])
    result = runner.invoke(cli.app, ["list-manual"])
    assert "no founders need a manual email" in result.output
