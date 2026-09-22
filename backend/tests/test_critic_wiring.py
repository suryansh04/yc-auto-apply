"""Task 13 (D22): `app.api.deps.build_critic` is the ONE provider switch
for a `Critic` adapter, shared by `app.cli._critic` (the CLI's `draft`
command) and `app.api.jobs._draft` (the UI job runner) — the same
extraction `build_drafter`/`build_classifier` already got (T6-6). These
tests build a critic through BOTH callers for the same `LLM_PROVIDER` and
assert they agree, exactly mirroring `tests/test_drafter_wiring.py`."""

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_build_critic_and_the_cli_critic_agree_on_gemini(monkeypatch):
    import app.cli as cli
    from app.adapters.gemini import GeminiCritic
    from app.api.deps import build_critic

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    get_settings.cache_clear()

    via_deps = build_critic(get_settings())
    via_cli = cli._critic()

    assert type(via_deps) is GeminiCritic
    assert type(via_cli) is GeminiCritic
    assert type(via_deps) is type(via_cli)


def test_build_critic_and_the_cli_critic_agree_on_sarvam(monkeypatch):
    import app.cli as cli
    from app.adapters.sarvam import SarvamCritic
    from app.api.deps import build_critic

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("LLM_PROVIDER", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "sk_not_real")
    get_settings.cache_clear()

    via_deps = build_critic(get_settings())
    via_cli = cli._critic()

    assert type(via_deps) is SarvamCritic
    assert type(via_cli) is SarvamCritic
    assert type(via_deps) is type(via_cli)


def test_build_critic_names_the_missing_sarvam_key_and_the_cli_translates_it():
    import typer

    import app.cli as cli
    from app.api.deps import build_critic

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
        monkeypatch.setenv("LLM_PROVIDER", "sarvam")
        monkeypatch.delenv("SARVAM_API_KEY", raising=False)
        get_settings.cache_clear()

        with pytest.raises(RuntimeError) as exc_info:
            build_critic(get_settings())
        assert type(exc_info.value) is RuntimeError  # not typer.BadParameter - no CLI dependency
        assert "SARVAM_API_KEY" in str(exc_info.value)

        with pytest.raises(typer.BadParameter) as cli_exc_info:
            cli._critic()
        assert "SARVAM_API_KEY" in str(cli_exc_info.value)


class _StubSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass


def test_draft_command_wires_a_critic_into_draftoutreach(monkeypatch, tmp_path):
    """The CLI's `draft` command actually passes a critic through to
    `DraftOutreach` (not just able to build one) — this is the guard that
    fails if `cli.py draft` forgets to pass `critic=critic` at its call
    site. `_critic()`/`_session()`/every repo the command constructs are
    patched to recorders or no-op stubs (same pattern as
    `tests/test_cli.py`'s own `_StubSession`) so the command reaches
    `DraftOutreach(...)` with no real database or LLM client involved."""
    import app.cli as cli
    from app.adapters.db import repositories
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GEMINI_TIER", "paid")
    get_settings.cache_clear()

    sentinel = object()
    captured = {}

    class _RecordingDraftOutreach:
        def __init__(self, drafter, repos, keep_unverified=False, critic=None):
            captured["critic"] = critic

        def run(self, limit=None):
            raise SystemExit(0)  # stop the command right after construction

    for repo_name in (
        "CompanyLaunchRepo", "CompanyNewsRepo", "CompanyRepo", "FounderRepo",
        "JobPostingRepo", "MessageRepo", "OutreachRepo", "ResumeProfileRepo",
    ):
        monkeypatch.setattr(repositories, repo_name, lambda session: object())

    monkeypatch.setattr(cli, "_session", lambda: _StubSession())
    monkeypatch.setattr(cli, "_drafter", lambda: object())
    monkeypatch.setattr(cli, "_critic", lambda: sentinel)
    monkeypatch.setattr(
        "app.application.draft_outreach.DraftOutreach", _RecordingDraftOutreach
    )

    from typer.testing import CliRunner

    result = CliRunner().invoke(cli.app, ["draft"])
    assert captured.get("critic") is sentinel, result.exception
