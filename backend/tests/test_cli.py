"""Fix round 1 of Tasks 13/14 (F2, ruling R4): the CLI's free-tier policy.

`draft` ships founders' names/titles/bios to Gemini, so on `GEMINI_TIER=free`
it must refuse before a client is even built unless `--allow-free-tier` is
passed. `parse-resume` sends only the user's own resume and merely warns.
Both are exercised through Typer's `CliRunner` with `_gemini_client`
monkeypatched to a recorder that never touches the network.
"""

import pytest
from typer.testing import CliRunner

import app.cli as cli
from app.config import get_settings

runner = CliRunner()


class _ClientBuilt(Exception):
    """Raised by the patched client factory so the command stops right after
    the guard without needing a database."""


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    get_settings.cache_clear()
    calls = []

    def fake_client():
        calls.append(True)
        raise _ClientBuilt()

    monkeypatch.setattr(cli, "_gemini_client", fake_client)
    yield calls
    get_settings.cache_clear()


def test_draft_refuses_on_the_free_tier_before_building_a_client(monkeypatch, env):
    monkeypatch.setenv("GEMINI_TIER", "free")
    get_settings.cache_clear()
    result = runner.invoke(cli.app, ["draft", "--limit", "5"])
    assert result.exit_code != 0
    assert "free" in result.output.lower() and "founder" in result.output.lower()
    assert env == []                                    # no client was ever built


def test_draft_proceeds_on_the_free_tier_with_the_explicit_opt_in(monkeypatch, env):
    monkeypatch.setenv("GEMINI_TIER", "free")
    get_settings.cache_clear()
    result = runner.invoke(cli.app, ["draft", "--limit", "5", "--allow-free-tier"])
    assert "refusing" not in result.output.lower()
    assert env == [True]                                # got as far as building the client
    assert isinstance(result.exception, _ClientBuilt)


def test_draft_proceeds_on_the_paid_tier_without_a_flag(monkeypatch, env):
    monkeypatch.setenv("GEMINI_TIER", "paid")
    get_settings.cache_clear()
    result = runner.invoke(cli.app, ["draft"])
    assert "refusing" not in result.output.lower()
    assert env == [True]


def test_parse_resume_only_warns_on_the_free_tier(monkeypatch, env, tmp_path):
    monkeypatch.setenv("GEMINI_TIER", "free")
    get_settings.cache_clear()
    resume = tmp_path / "resume.txt"
    resume.write_text("Built a scheduler in Python", encoding="utf-8")
    result = runner.invoke(cli.app, ["parse-resume", str(resume)])
    assert "warning" in result.output.lower() and "free" in result.output.lower()
    assert env == [True]                                # warned, then carried on


def test_parse_resume_does_not_warn_on_the_paid_tier(monkeypatch, env, tmp_path):
    monkeypatch.setenv("GEMINI_TIER", "paid")
    get_settings.cache_clear()
    resume = tmp_path / "resume.txt"
    resume.write_text("Built a scheduler in Python", encoding="utf-8")
    result = runner.invoke(cli.app, ["parse-resume", str(resume)])
    assert "warning" not in result.output.lower()
    assert env == [True]
