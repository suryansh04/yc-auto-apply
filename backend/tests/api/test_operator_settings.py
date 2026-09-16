"""Live finding (2026-09-16): `/api/stats`, `/api/queue` and the offline
`/api/send` path read `os.environ` directly to dodge `Settings.database_url`
being required. Under uvicorn nothing exports `backend/.env`, so the review
queue previewed drafts WITHOUT the signature `send` then appended, and the
verifier showed as unconfigured. `get_operator_settings()` honours `.env`
without requiring a DB; these tests pin that the routes go through it.
"""

import pytest

from app.config import Settings, get_operator_settings


@pytest.fixture
def dotenv_only(monkeypatch, tmp_path):
    """Values present ONLY in a .env file - no DATABASE_URL anywhere."""
    env = tmp_path / ".env"
    env.write_text(
        "SIGNATURE=Only In Dotenv\nEMAIL_VERIFIER_API_KEY=k\nDAILY_SEND_CAP=3\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SIGNATURE", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", str(env))
    get_operator_settings.cache_clear()
    yield
    get_operator_settings.cache_clear()


def test_operator_settings_load_dotenv_without_a_database_url(dotenv_only):
    s = get_operator_settings()
    assert s.signature == "Only In Dotenv"
    assert s.email_verifier_api_key == "k"
    assert s.daily_send_cap == 3


def test_queue_preview_signature_comes_from_dotenv(dotenv_only, client, pending_draft):
    item = client.get("/api/queue").json()[0]
    assert "\n\nOnly In Dotenv\n\n" in item["wire_text"]


def test_stats_verifier_configured_comes_from_dotenv(dotenv_only, client):
    assert client.get("/api/stats").json()["quota"]["configured"] is True
