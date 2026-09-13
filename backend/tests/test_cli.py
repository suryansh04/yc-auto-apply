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

    def fake_client(*args, **kwargs):
        calls.append(True)
        raise _ClientBuilt()

    # Fix round 2 (ruling T6-6): `_drafter` now delegates to
    # `app.api.deps.build_drafter`, which builds its own `genai.Client`
    # rather than going through `cli._gemini_client` - so the interception
    # point moves to the actual class both `cli._gemini_client` (still
    # used by `_resume_parser`) and `build_drafter` construct. Patching
    # `google.genai.Client` itself catches either caller, same as
    # patching `cli._gemini_client` did before the extraction.
    monkeypatch.setattr("google.genai.Client", fake_client)
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


# --- Task 15: `send` refuses to build a sender or open a session until every
# send-side setting is present; all four default to empty in `app.config`.

import json  # noqa: E402


class _SessionOpened(Exception):
    """Raised by the patched session factory: the guard passed and the
    command reached the database step (which needs no DB here)."""


@pytest.fixture
def send_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    for var in ("SIGNATURE", "RESUME_PATH", "GMAIL_SENDER_ADDRESS", "GMAIL_CLIENT_SECRETS_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env file can supply the settings
    get_settings.cache_clear()
    built = []

    def fake_sender(*a, **kw):
        built.append(kw)
        raise AssertionError("no sender may be built before the guard passes")

    def fake_session():
        raise _SessionOpened()

    monkeypatch.setattr(cli, "_gmail_sender", fake_sender)
    monkeypatch.setattr(cli, "_session", fake_session)
    yield built
    get_settings.cache_clear()


def _complete_send_config(monkeypatch, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}),
                       encoding="utf-8")
    monkeypatch.setenv("SIGNATURE", "Suryansh")
    monkeypatch.setenv("RESUME_PATH", str(resume))
    monkeypatch.setenv("GMAIL_SENDER_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_PATH", str(secrets))
    get_settings.cache_clear()


def test_send_refuses_with_no_send_configuration(send_env):
    result = runner.invoke(cli.app, ["send"])
    assert result.exit_code == 2
    out = result.output
    assert "refusing to send" in out
    for name in ("SIGNATURE", "RESUME_PATH", "GMAIL_SENDER_ADDRESS", "GMAIL_CLIENT_SECRETS_PATH"):
        assert name in out
    assert send_env == []
    assert not isinstance(result.exception, _SessionOpened)


@pytest.mark.parametrize("missing", ["SIGNATURE", "RESUME_PATH", "GMAIL_SENDER_ADDRESS",
                                     "GMAIL_CLIENT_SECRETS_PATH"])
def test_send_refuses_when_any_one_setting_is_missing(monkeypatch, tmp_path, send_env, missing):
    _complete_send_config(monkeypatch, tmp_path)
    monkeypatch.setenv(missing, "")
    get_settings.cache_clear()
    result = runner.invoke(cli.app, ["send", "--to-self"])
    assert result.exit_code == 2
    assert missing in result.output
    assert send_env == []


def test_send_refuses_a_whitespace_only_signature(monkeypatch, tmp_path, send_env):
    """Fix round 1 (F2): pins the `.strip()` in the guard - `SIGNATURE="  "`
    is not a real signature (spec §18)."""
    _complete_send_config(monkeypatch, tmp_path)
    monkeypatch.setenv("SIGNATURE", "   ")
    get_settings.cache_clear()
    result = runner.invoke(cli.app, ["send"])
    assert result.exit_code == 2
    assert "SIGNATURE" in result.output
    assert send_env == []


def test_send_refuses_a_resume_path_that_does_not_exist(monkeypatch, tmp_path, send_env):
    _complete_send_config(monkeypatch, tmp_path)
    monkeypatch.setenv("RESUME_PATH", str(tmp_path / "missing.pdf"))
    get_settings.cache_clear()
    result = runner.invoke(cli.app, ["send"])
    assert result.exit_code == 2 and "RESUME_PATH" in result.output


def test_send_with_complete_config_reaches_the_database_step(monkeypatch, tmp_path, send_env):
    _complete_send_config(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["send", "--limit", "1"])
    assert "refusing" not in result.output
    assert isinstance(result.exception, _SessionOpened)   # guard passed; stopped at the DB
    assert send_env == []                                   # no sender before credentials are loaded


def test_send_refuses_a_client_secrets_file_without_a_client(monkeypatch, tmp_path, send_env):
    _complete_send_config(monkeypatch, tmp_path)
    (tmp_path / "client_secret.json").write_text(json.dumps({"installed": {}}), encoding="utf-8")
    result = runner.invoke(cli.app, ["send"])
    assert result.exit_code != 0
    assert not isinstance(result.exception, _SessionOpened)


def test_load_oauth_client_reads_installed_or_web_blocks(tmp_path):
    p = tmp_path / "web.json"
    p.write_text(json.dumps({"web": {"client_id": "w-id", "client_secret": "w-secret"}}), encoding="utf-8")
    assert cli._load_oauth_client(str(p)) == ("w-id", "w-secret")


def test_gmail_auth_requires_the_gmail_configuration(send_env):
    result = runner.invoke(cli.app, ["gmail-auth"])
    assert result.exit_code == 2
    assert "refusing" in result.output
    assert "GMAIL_SENDER_ADDRESS" in result.output and "GMAIL_CLIENT_SECRETS_PATH" in result.output


class _FlowStarted(Exception):
    """Raised by the patched OAuth flow: `gmail-auth` passed its guard."""


def test_gmail_auth_does_not_need_the_resume_or_signature(monkeypatch, tmp_path, send_env):
    """Fix round 1 (F5): authorising Gmail is a separate setup step from
    having the resume and signature in place."""
    import google_auth_oauthlib.flow as flow_module

    _complete_send_config(monkeypatch, tmp_path)
    monkeypatch.setenv("SIGNATURE", "")
    monkeypatch.setenv("RESUME_PATH", "")
    get_settings.cache_clear()

    class _Flow:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            raise _FlowStarted()

    monkeypatch.setattr(flow_module, "InstalledAppFlow", _Flow)
    result = runner.invoke(cli.app, ["gmail-auth"])
    assert "refusing" not in result.output
    assert isinstance(result.exception, _FlowStarted)


# --- Fix round 1 (F1): `--to-self` routing. The reviewer's mutant "route
# `--to-self` to `run()`" - the operator's smoke test emailing every founder -
# survived the suite because every CLI test stopped at the patched session.
# These drive `send` past the session and credential lookup with a spy in
# place of `SendApproved`.


class _StubSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubCredentialRepo:
    def __init__(self, session):
        pass

    def get_by_account_email(self, account_email):
        return type("Cred", (), {"refresh_token": "1//stub-refresh-token"})()


class _StubSender:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _SpySendApproved:
    """Records which entry point the CLI calls, and with what."""
    instances: list = []

    def __init__(self, sender, clock, repos, **kwargs):
        self.kwargs = kwargs
        self.run_calls: list[dict] = []
        self.to_self_calls: list[dict] = []
        _SpySendApproved.instances.append(self)

    def run(self, cap, limit=None):
        self.run_calls.append({"cap": cap, "limit": limit})
        from app.application.send_approved import SendResult
        return SendResult(sent=1)

    def send_to_self(self, address, limit=1):
        self.to_self_calls.append({"address": address, "limit": limit})
        return limit


@pytest.fixture
def wired_send(monkeypatch, tmp_path, send_env):
    import app.adapters.db.repositories as repositories
    import app.application.send_approved as send_approved

    _complete_send_config(monkeypatch, tmp_path)
    monkeypatch.setenv("DAILY_SEND_CAP", "7")
    get_settings.cache_clear()
    sender = _StubSender()
    monkeypatch.setattr(cli, "_session", lambda: _StubSession())
    monkeypatch.setattr(cli, "_gmail_sender", lambda *a, **kw: sender)
    monkeypatch.setattr(repositories, "GmailCredentialRepo", _StubCredentialRepo)
    monkeypatch.setattr(send_approved, "SendApproved", _SpySendApproved)
    _SpySendApproved.instances.clear()
    yield sender
    _SpySendApproved.instances.clear()


def _spy() -> _SpySendApproved:
    assert len(_SpySendApproved.instances) == 1
    return _SpySendApproved.instances[0]


def test_send_to_self_calls_send_to_self_and_never_run(wired_send):
    result = runner.invoke(cli.app, ["send", "--to-self"])
    assert result.exit_code == 0, result.output
    spy = _spy()
    assert spy.to_self_calls == [{"address": "me@gmail.com", "limit": 1}]
    assert spy.run_calls == []
    assert "nothing was reserved" in result.output
    assert wired_send.closed


def test_send_without_the_flag_calls_run_with_the_daily_cap_and_never_send_to_self(wired_send):
    result = runner.invoke(cli.app, ["send"])
    assert result.exit_code == 0, result.output
    spy = _spy()
    assert spy.run_calls == [{"cap": 7, "limit": None}]
    assert spy.to_self_calls == []
    assert wired_send.closed


def test_send_passes_limit_through_to_run(wired_send):
    result = runner.invoke(cli.app, ["send", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert _spy().run_calls == [{"cap": 7, "limit": 2}]


def test_send_to_self_limit_is_clamped_to_three(wired_send):
    """Fix round 1 (F8): copies to yourself bypass the DB cap but not
    Gmail's real quota and spam heuristics."""
    result = runner.invoke(cli.app, ["send", "--to-self", "--limit", "50"])
    assert result.exit_code == 0, result.output
    assert _spy().to_self_calls == [{"address": "me@gmail.com", "limit": 3}]


def test_send_to_self_limit_below_the_clamp_is_honoured(wired_send):
    runner.invoke(cli.app, ["send", "--to-self", "--limit", "2"])
    assert _spy().to_self_calls == [{"address": "me@gmail.com", "limit": 2}]


def test_send_to_self_help_states_the_clamp():
    result = runner.invoke(cli.app, ["send", "--help"])
    assert "clamped to 3" in result.output


def test_send_wires_signature_and_resume_into_the_use_case(wired_send, tmp_path):
    runner.invoke(cli.app, ["send"])
    kwargs = _spy().kwargs
    assert kwargs["signature"] == "Suryansh"
    assert kwargs["attachment_path"] == str(tmp_path / "resume.pdf")
    assert kwargs["pause_between_sends"] is cli._random_pause


# --- LLM provider switch (2026-09-13): Gemini free tier is 20 req/day -------


class _SarvamBuilt(Exception):
    pass


@pytest.fixture
def sarvam_env(monkeypatch, env):
    monkeypatch.setenv("LLM_PROVIDER", "sarvam")
    monkeypatch.setenv("SARVAM_API_KEY", "sk_not_real")
    monkeypatch.setenv("GEMINI_TIER", "free")   # must be irrelevant on Sarvam
    get_settings.cache_clear()
    built = []

    def fake_parser(*a, **kw):
        built.append(("parser", kw.get("model")))
        raise _SarvamBuilt()

    def fake_drafter(*a, **kw):
        built.append(("drafter", kw.get("model")))
        raise _SarvamBuilt()

    monkeypatch.setattr("app.adapters.sarvam.SarvamResumeParser", fake_parser)
    monkeypatch.setattr("app.adapters.sarvam.SarvamDrafter", fake_drafter)
    yield built
    get_settings.cache_clear()


def test_draft_on_sarvam_builds_the_sarvam_drafter_and_ignores_the_gemini_tier(sarvam_env, env):
    result = runner.invoke(cli.app, ["draft", "--limit", "5"])
    assert "refusing" not in result.output.lower()
    assert isinstance(result.exception, _SarvamBuilt)
    assert sarvam_env == [("drafter", "sarvam-105b-conversations")]
    assert env == []                                    # no Gemini client built


def test_parse_resume_on_sarvam_builds_the_sarvam_parser(sarvam_env, env, tmp_path):
    p = tmp_path / "r.txt"
    p.write_text("resume", encoding="utf-8")
    result = runner.invoke(cli.app, ["parse-resume", str(p)])
    assert isinstance(result.exception, _SarvamBuilt)
    assert sarvam_env == [("parser", "sarvam-105b-conversations")]
    assert "GEMINI_TIER" not in result.output
    assert env == []


def test_sarvam_provider_refuses_without_a_key(monkeypatch, env):
    monkeypatch.setenv("LLM_PROVIDER", "sarvam")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    get_settings.cache_clear()
    result = runner.invoke(cli.app, ["draft"])
    assert result.exit_code != 0
    assert "SARVAM_API_KEY" in result.output
    assert env == []


def test_sarvam_model_is_taken_from_settings(monkeypatch, sarvam_env, env):
    monkeypatch.setenv("SARVAM_MODEL", "sarvam-future")
    get_settings.cache_clear()
    runner.invoke(cli.app, ["draft"])
    assert sarvam_env == [("drafter", "sarvam-future")]


def test_gemini_remains_the_default_provider(env):
    assert get_settings().llm_provider == "gemini"
