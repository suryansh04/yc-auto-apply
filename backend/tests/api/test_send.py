"""`POST /api/send` over HTTP - the browser peer of `app.cli`'s `send`.
Same `SendApproved` use case; `fake_sender` (tests/conftest.py) is the only
way this route is ever reached with something other than a real Gmail
client, and it is what proves this suite can never send a real email."""


def test_send_sends_approved_rows_and_reports_counts(client, repos, approved_draft, fake_sender):
    body = client.post("/api/send", json={"limit": 5}).json()
    assert body["sent"] == 1
    assert repos.outreach.get(approved_draft.outreach.id).state.value == "sent"


def test_send_respects_the_daily_cap(client, repos, five_approved, fake_sender, monkeypatch):
    # Fix round 1 (review item 1): `sent + skipped_cap == 5` holds even with
    # no cap enforced at all - pin an explicit cap below the row count so
    # disabling the cap actually fails this test.
    monkeypatch.setenv("DAILY_SEND_CAP", "3")
    body = client.post("/api/send", json={}).json()
    assert body["sent"] == 3
    assert body["skipped_cap"] == 2


def test_send_never_sends_a_pending_draft(client, repos, pending_draft, fake_sender):
    assert client.post("/api/send", json={}).json()["sent"] == 0
    assert fake_sender.sent == []


import pytest

# --- Final fix wave (F7 / F9): the real-sender builder ----------------------
#
# `_build_real_sender` is only reachable when no fake sender is injected,
# so it is exercised directly here with `require_send_config` satisfied
# and the credential repo stubbed - no DB, no network.


def _settings(secrets_path):
    from types import SimpleNamespace

    return SimpleNamespace(
        gmail_client_secrets_path=str(secrets_path),
        gmail_sender_address="operator@example.com",
        signature="Suryansh", resume_path="unused.pdf", daily_send_cap=5,
    )


def test_a_malformed_client_secrets_file_is_400_without_its_contents(monkeypatch, tmp_path):
    """F7: the router no longer carries its own copy of the loader - it
    goes through `app.api.deps.load_oauth_client` (the one shared with
    `app.cli` and the poll-replies job) and translates its `RuntimeError`
    into a fixed 400. The file's contents never reach the body."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.routers import send as send_module

    secrets_path = tmp_path / "client_secret.json"
    secrets_path.write_text('{"installed": {"client_id": "only-an-id-GOCSPX-nothing-else"}}', encoding="utf-8")
    monkeypatch.setattr(send_module, "require_send_config", lambda settings: [])

    with pytest.raises(HTTPException) as exc_info:
        send_module._build_real_sender(SimpleNamespace(session=None), _settings(secrets_path))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "gmail client secrets file is malformed"
    assert "GOCSPX" not in str(exc_info.value.detail)


def test_the_send_router_uses_the_shared_oauth_loader(monkeypatch, tmp_path):
    """F7, pinned structurally: the router must call `deps.load_oauth_client`
    - patching that one name is enough to satisfy the loader step, which
    proves no private copy is consulted."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.routers import send as send_module

    calls = []
    monkeypatch.setattr(send_module, "require_send_config", lambda settings: [])
    monkeypatch.setattr(send_module, "load_oauth_client", lambda path: calls.append(path) or ("cid", "csecret"))

    class _NoCredentialRepo:
        def __init__(self, session):
            pass

        def get_by_account_email(self, account_email):
            return None

    monkeypatch.setattr("app.adapters.db.repositories.GmailCredentialRepo", _NoCredentialRepo)

    with pytest.raises(HTTPException):
        send_module._build_real_sender(SimpleNamespace(session=None), _settings(tmp_path / "does-not-matter"))
    assert calls == [str(tmp_path / "does-not-matter")]


def test_a_missing_refresh_token_is_400_without_the_account_address(monkeypatch, tmp_path):
    """F9: the sender address is configuration identity (T8's rule:
    never in a raised message or an HTTP body). The 400 is a FIXED
    message that tells the operator what to run, not which account."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.routers import send as send_module

    secrets_path = tmp_path / "client_secret.json"
    secrets_path.write_text('{"installed": {"client_id": "cid", "client_secret": "csecret"}}', encoding="utf-8")
    monkeypatch.setattr(send_module, "require_send_config", lambda settings: [])

    class _NoCredentialRepo:
        def __init__(self, session):
            pass

        def get_by_account_email(self, account_email):
            return None

    monkeypatch.setattr("app.adapters.db.repositories.GmailCredentialRepo", _NoCredentialRepo)

    with pytest.raises(HTTPException) as exc_info:
        send_module._build_real_sender(SimpleNamespace(session=None), _settings(secrets_path))

    assert exc_info.value.status_code == 400
    detail = str(exc_info.value.detail)
    assert "operator@example.com" not in detail
    assert "gmail-auth" in detail
