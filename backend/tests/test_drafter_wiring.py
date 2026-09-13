"""Fix round 2 (ruling T6-6): `app.api.deps.build_drafter` is the ONE
provider switch for a `Drafter` adapter, shared by `app.cli._drafter`
(the CLI's `draft` command) and `app.api.jobs._draft` (the UI job
runner). Before this extraction each caller had its own copy of the
Sarvam/Gemini switch - untested duplication that a new provider or a
changed model default could silently leave out of sync between them.

These tests build a drafter through BOTH callers for the same
`LLM_PROVIDER` and assert they agree - not just on class name, but on
the actual type object, which fails the instant one caller's copy
drifts to a different adapter or wraps it differently. No network call:
`genai.Client(api_key=...)` and `SarvamDrafter.__init__` only validate
locally (a placeholder key is enough), confirmed by running these
against the real adapters, not mocks."""

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_build_drafter_and_the_cli_drafter_agree_on_gemini(monkeypatch):
    import app.cli as cli
    from app.adapters.gemini import GeminiDrafter
    from app.api.deps import build_drafter

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    get_settings.cache_clear()

    via_deps = build_drafter(get_settings())
    via_cli = cli._drafter()

    assert type(via_deps) is GeminiDrafter
    assert type(via_cli) is GeminiDrafter
    assert type(via_deps) is type(via_cli)


def test_build_drafter_and_the_cli_drafter_agree_on_sarvam(monkeypatch):
    import app.cli as cli
    from app.adapters.sarvam import SarvamDrafter
    from app.api.deps import build_drafter

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("LLM_PROVIDER", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "sk_not_real")
    get_settings.cache_clear()

    via_deps = build_drafter(get_settings())
    via_cli = cli._drafter()

    assert type(via_deps) is SarvamDrafter
    assert type(via_cli) is SarvamDrafter
    assert type(via_deps) is type(via_cli)


def test_build_drafter_names_the_missing_sarvam_key_and_the_cli_translates_it(monkeypatch):
    """`build_drafter` must stay usable from `app/api/` with no `typer`
    dependency (it raises a plain `RuntimeError`); `cli._drafter`
    translates that into `typer.BadParameter` for the CLI's own error
    formatting - both behaviours pinned in one test so a future change
    can't quietly drop either half."""
    import typer

    import app.cli as cli
    from app.api.deps import build_drafter

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("LLM_PROVIDER", "sarvam")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError) as exc_info:
        build_drafter(get_settings())
    assert type(exc_info.value) is RuntimeError  # not typer.BadParameter - no CLI dependency
    assert "SARVAM_API_KEY" in str(exc_info.value)

    with pytest.raises(typer.BadParameter) as cli_exc_info:
        cli._drafter()
    assert "SARVAM_API_KEY" in str(cli_exc_info.value)
