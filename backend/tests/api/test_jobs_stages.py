"""Task 15a: the three stages left unregistered by Tasks 8, 9 and 11
(`poll-replies`, `classify-replies`, `schedule-follow-ups`) now have their
own `JOB_KINDS` entries in `app.api.jobs`. Exercised directly, the same
way `tests/api/test_jobs_draft_stage.py` exercises `_draft` - every repo
class, builder and use case the stage function imports locally is patched
to a no-op stand-in or a recorder, no HTTP, no real DB.
"""
from types import SimpleNamespace

from app.config import get_settings


class _StubSession:
    def commit(self):
        pass


def _set_base_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GEMINI_TIER", "paid")
    get_settings.cache_clear()


def test_job_kinds_has_exactly_the_seven_registered_stages():
    from app.api.jobs import JOB_KINDS

    assert set(JOB_KINDS) == {
        "sync",
        "scrape",
        "resolve",
        "draft",
        "poll-replies",
        "classify-replies",
        "schedule-follow-ups",
    }


# --- poll-replies -----------------------------------------------------------


def test_poll_replies_stage_refuses_without_client_secrets_path(monkeypatch, caplog):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_PATH", "")
    monkeypatch.setenv("GMAIL_SENDER_ADDRESS", "")
    get_settings.cache_clear()

    from app.api import jobs

    try:
        jobs._poll_replies(_StubSession(), lambda done, total=None: None)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "GMAIL_CLIENT_SECRETS_PATH" in str(exc)


def test_poll_replies_stage_refuses_without_a_stored_credential(monkeypatch, tmp_path):
    _set_base_env(monkeypatch)
    secrets_path = tmp_path / "client_secret.json"
    secrets_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_PATH", str(secrets_path))
    monkeypatch.setenv("GMAIL_SENDER_ADDRESS", "operator@example.com")
    get_settings.cache_clear()

    from app.adapters.db import repositories
    from app.api import jobs

    class _NoCredentialRepo:
        def __init__(self, session):
            pass

        def get_by_account_email(self, account_email):
            return None

    monkeypatch.setattr(repositories, "GmailCredentialRepo", _NoCredentialRepo)

    try:
        jobs._poll_replies(_StubSession(), lambda done, total=None: None)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        message = str(exc)
        assert "operator@example.com" not in message  # never leak the value
        assert "credential" in message.lower()


def test_poll_replies_stage_wires_gmailreader_and_pollrepos(monkeypatch, tmp_path):
    _set_base_env(monkeypatch)
    secrets_path = tmp_path / "client_secret.json"
    secrets_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_PATH", str(secrets_path))
    monkeypatch.setenv("GMAIL_SENDER_ADDRESS", "operator@example.com")
    get_settings.cache_clear()

    from app.adapters.db import repositories
    from app.api import deps, jobs

    credential_sentinel = object()

    class _CredentialRepo:
        def __init__(self, session):
            pass

        def get_by_account_email(self, account_email):
            return credential_sentinel

    for repo_name in ("FounderRepo", "MessageRepo", "OutreachRepo"):
        monkeypatch.setattr(repositories, repo_name, lambda session: object())
    monkeypatch.setattr(repositories, "GmailCredentialRepo", _CredentialRepo)
    monkeypatch.setattr(deps, "load_oauth_client", lambda path: ("cid", "csecret"))

    captured = {}

    class _FakeReader:
        def __init__(self, credential_repo, account, client_id, client_secret):
            captured["credential_repo"] = credential_repo
            captured["account"] = account
            captured["client_id"] = client_id
            captured["client_secret"] = client_secret

        def close(self):
            captured["closed"] = True

    class _RecordingPollReplies:
        def __init__(self, reader, repos, clock):
            captured["reader"] = reader
            captured["repos"] = repos

        def run(self):
            return SimpleNamespace(fetched=3, stored=2, bounced=1, skipped=0)

    monkeypatch.setattr("app.adapters.gmail_reader.GmailReader", _FakeReader)
    monkeypatch.setattr("app.application.poll_replies.PollReplies", _RecordingPollReplies)

    summary = jobs._poll_replies(_StubSession(), lambda done, total=None: None)

    assert captured["account"] == "operator@example.com"
    assert captured["client_id"] == "cid"
    assert captured["client_secret"] == "csecret"
    assert captured["closed"] is True
    assert summary == "fetched 3, stored 2, bounced 1, skipped 0"


# --- classify-replies --------------------------------------------------------


def test_classify_replies_stage_refuses_on_free_tier_before_building_anything(monkeypatch):
    _set_base_env(monkeypatch)
    monkeypatch.setenv("GEMINI_TIER", "free")
    get_settings.cache_clear()

    from app.api import deps, jobs

    def _boom(settings):
        raise AssertionError("build_classifier must not be called on a free-tier key")

    monkeypatch.setattr(deps, "build_classifier", _boom)

    try:
        jobs._classify_replies(_StubSession(), lambda done, total=None: None)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "free-tier" in str(exc).lower()


def test_classify_replies_stage_wires_repos_and_returns_counts(monkeypatch):
    _set_base_env(monkeypatch)
    get_settings.cache_clear()

    from app.adapters.db import repositories
    from app.api import deps, jobs

    for repo_name in ("MessageRepo", "OutreachRepo", "SettingsRepo"):
        monkeypatch.setattr(repositories, repo_name, lambda session: object())

    classifier_sentinel = object()
    monkeypatch.setattr(deps, "build_classifier", lambda settings: classifier_sentinel)
    monkeypatch.setattr(deps, "on_gemini_free_tier", lambda settings: False)

    captured = {}

    class _RecordingClassifyReply:
        def __init__(self, classifier, repos, clock):
            captured["classifier"] = classifier
            captured["repos"] = repos

        def run(self):
            return SimpleNamespace(classified=4, replied=2, ignored=1, errors=1)

    monkeypatch.setattr(
        "app.application.classify_reply.ClassifyReply", _RecordingClassifyReply
    )

    summary = jobs._classify_replies(_StubSession(), lambda done, total=None: None)

    assert captured["classifier"] is classifier_sentinel
    assert summary == "classified 4, replied 2, ignored 1, errors 1"


# --- schedule-follow-ups ------------------------------------------------------


def test_schedule_follow_ups_stage_wires_repos_and_returns_counts(monkeypatch):
    _set_base_env(monkeypatch)
    get_settings.cache_clear()

    from app.adapters.db import repositories
    from app.api import jobs

    for repo_name in ("MessageRepo", "OutreachRepo", "SettingsRepo"):
        monkeypatch.setattr(repositories, repo_name, lambda session: object())

    captured = {}

    class _RecordingScheduleFollowUps:
        def __init__(self, repos, clock):
            captured["repos"] = repos

        def run(self):
            return SimpleNamespace(scheduled=5, closed=2)

    monkeypatch.setattr(
        "app.application.schedule_follow_ups.ScheduleFollowUps",
        _RecordingScheduleFollowUps,
    )

    summary = jobs._schedule_follow_ups(_StubSession(), lambda done, total=None: None)

    assert captured["repos"].settings is not None
    assert summary == "scheduled 5, closed 2"
