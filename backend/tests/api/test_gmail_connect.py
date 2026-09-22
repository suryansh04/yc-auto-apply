"""Connecting Gmail from the browser instead of the terminal.

Everything here runs offline: `create_app`'s `gmail_oauth_factory` seam
takes a fake flow, so the consent URL, the code exchange and every
failure branch are exercised without a browser, a client-secrets file or
a network call.
"""

import pytest

from app.adapters.gmail_oauth import (
    GMAIL_SCOPES,
    AuthorizationRequest,
    GmailTokens,
    MissingScope,
    NoRefreshToken,
)
from app.config import Settings, get_operator_settings

SENDER = "operator@example.com"


class FakeFlow:
    """Stands in for `GoogleOAuthFlow`. Records what it was asked, so a
    test can assert the state actually reached Google's URL."""

    def __init__(self, tokens=None, raises=None):
        self._tokens = tokens or GmailTokens(
            refresh_token="refresh-token-value",
            token_uri="https://oauth2.googleapis.com/token",
            scopes=list(GMAIL_SCOPES),
        )
        self._raises = raises
        self.states: list[str] = []
        self.codes: list[str] = []
        self.verifiers: list[str | None] = []
        self.redirect_uris: list[str] = []

    def authorization_url(self, state):
        self.states.append(state)
        return AuthorizationRequest(
            url=f"https://accounts.google.com/o/oauth2/auth?state={state}",
            code_verifier=f"verifier-for-{state}",
        )

    def exchange_code(self, code, code_verifier=None):
        self.codes.append(code)
        self.verifiers.append(code_verifier)
        if self._raises:
            raise self._raises
        return self._tokens


@pytest.fixture
def gmail_client(client, monkeypatch, tmp_path):
    """`client` with Gmail configured (an address and a real file at the
    client-secrets path) and a fake flow behind the OAuth seam."""
    secrets_file = tmp_path / "client_secret.json"
    secrets_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GMAIL_SENDER_ADDRESS", SENDER)
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_PATH", str(secrets_file))
    monkeypatch.setenv("APP_URL", "http://localhost:5173")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_operator_settings.cache_clear()
    flow = FakeFlow()
    client.app.state.gmail_oauth_factory = lambda settings, redirect_uri: (
        flow.redirect_uris.append(redirect_uri) or flow
    )
    yield client, flow
    get_operator_settings.cache_clear()


# --- status ---------------------------------------------------------------

def test_status_reports_not_connected_before_any_consent(gmail_client):
    client, _ = gmail_client
    body = client.get("/api/gmail/status").json()
    assert body["configured"] is True
    assert body["connected"] is False
    assert body["account"] == SENDER


def test_status_reports_what_configuration_is_missing(client, monkeypatch):
    # Nothing the UI can fix itself, so it has to be able to say which
    # .env keys are absent rather than showing an empty Connect button.
    monkeypatch.delenv("GMAIL_SENDER_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_CLIENT_SECRETS_PATH", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_operator_settings.cache_clear()
    body = client.get("/api/gmail/status").json()
    assert body["configured"] is False
    assert body["missing_config"]
    assert body["connected"] is False
    get_operator_settings.cache_clear()


def test_status_never_returns_the_refresh_token(gmail_client, repos):
    client, _ = gmail_client
    repos.credential.upsert(
        account_email=SENDER, refresh_token="super-secret-refresh",
        token_uri="https://oauth2.googleapis.com/token", scopes=list(GMAIL_SCOPES),
    )
    raw = client.get("/api/gmail/status").text
    assert "super-secret-refresh" not in raw


def test_status_flags_a_send_only_token_as_unable_to_read(gmail_client, repos):
    # A token minted before the read scope existed looks connected and
    # fails only when the reply poller runs.
    client, _ = gmail_client
    repos.credential.upsert(
        account_email=SENDER, refresh_token="t",
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    body = client.get("/api/gmail/status").json()
    assert body["connected"] is True
    assert body["can_read_replies"] is False


# --- connect --------------------------------------------------------------

def test_connect_returns_a_google_url_carrying_a_state(gmail_client):
    client, flow = gmail_client
    body = client.post("/api/gmail/connect").json()
    assert body["auth_url"].startswith("https://accounts.google.com/")
    assert flow.states and flow.states[0] in body["auth_url"]


def test_connect_uses_the_apis_own_callback_as_the_redirect(gmail_client):
    client, flow = gmail_client
    client.post("/api/gmail/connect")
    assert flow.redirect_uris[0].endswith("/api/gmail/callback")


def test_connect_refuses_when_gmail_is_not_configured(client, monkeypatch):
    monkeypatch.delenv("GMAIL_SENDER_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_CLIENT_SECRETS_PATH", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_operator_settings.cache_clear()
    assert client.post("/api/gmail/connect").status_code == 409
    get_operator_settings.cache_clear()


def test_each_connect_mints_a_fresh_state(gmail_client):
    client, flow = gmail_client
    client.post("/api/gmail/connect")
    client.post("/api/gmail/connect")
    assert flow.states[0] != flow.states[1]


# --- callback -------------------------------------------------------------

def _connect_then_callback(client, flow, **params):
    client.post("/api/gmail/connect")
    query = {"code": "auth-code", "state": flow.states[-1], **params}
    return client.get("/api/gmail/callback", params=query, follow_redirects=False)


def test_callback_stores_the_refresh_token_and_returns_to_the_app(gmail_client, repos):
    client, flow = gmail_client
    response = _connect_then_callback(client, flow)
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:5173/?gmail=connected"
    stored = repos.credential.get_by_account_email(SENDER)
    assert stored.refresh_token == "refresh-token-value"
    assert set(stored.scopes) == set(GMAIL_SCOPES)


def test_callback_rejects_a_state_this_process_never_minted(gmail_client, repos):
    # The callback is an unauthenticated GET; anything on this machine
    # could hit it with a code of its own choosing.
    client, flow = gmail_client
    response = client.get(
        "/api/gmail/callback", params={"code": "x", "state": "forged"}, follow_redirects=False
    )
    assert response.headers["location"].endswith("gmail=expired")
    assert flow.codes == []
    assert repos.credential.get_by_account_email(SENDER) is None


def test_a_state_cannot_be_spent_twice(gmail_client):
    client, flow = gmail_client
    _connect_then_callback(client, flow)
    replay = client.get(
        "/api/gmail/callback",
        params={"code": "auth-code", "state": flow.states[-1]},
        follow_redirects=False,
    )
    assert replay.headers["location"].endswith("gmail=expired")


def test_callback_reports_a_cancelled_consent_without_calling_google(gmail_client):
    client, flow = gmail_client
    client.post("/api/gmail/connect")
    response = client.get(
        "/api/gmail/callback",
        params={"error": "access_denied", "state": flow.states[-1]},
        follow_redirects=False,
    )
    assert response.headers["location"].endswith("gmail=cancelled")
    assert flow.codes == []


def test_callback_reports_a_missing_refresh_token_as_its_own_outcome(gmail_client, repos):
    # Google withholds the refresh token when the account already granted
    # this client; the operator has to revoke, and the UI has to say so.
    client, flow = gmail_client
    flow._raises = NoRefreshToken("no refresh token")
    response = _connect_then_callback(client, flow)
    assert response.headers["location"].endswith("gmail=no-refresh-token")
    assert repos.credential.get_by_account_email(SENDER) is None


def test_a_failing_exchange_never_leaks_the_reason_to_the_browser(gmail_client):
    client, flow = gmail_client
    flow._raises = RuntimeError("invalid_client: secret abc123 rejected")
    response = _connect_then_callback(client, flow)
    location = response.headers["location"]
    assert location.endswith("gmail=failed")
    assert "abc123" not in location
    assert "invalid_client" not in location


def test_the_authorisation_code_is_never_echoed_back_to_the_browser(gmail_client):
    client, flow = gmail_client
    response = _connect_then_callback(client, flow)
    assert "auth-code" not in response.headers["location"]


def test_callback_reports_a_partly_granted_consent_as_its_own_outcome(gmail_client, repos):
    # Google's screen lets each permission be unticked. A token missing
    # the read scope would send mail and then fail every poll, so it is
    # never stored.
    client, flow = gmail_client
    flow._raises = MissingScope("https://www.googleapis.com/auth/gmail.readonly")
    response = _connect_then_callback(client, flow)
    assert response.headers["location"].endswith("gmail=missing-scope")
    assert repos.credential.get_by_account_email(SENDER) is None


def test_the_pkce_verifier_from_connect_reaches_the_exchange(gmail_client):
    # Google answers "invalid_grant: Missing code verifier" otherwise: the
    # verifier is generated while building the consent URL and a fresh
    # flow object is built per request, so it has to be carried across.
    client, flow = gmail_client
    _connect_then_callback(client, flow)
    assert flow.verifiers == [f"verifier-for-{flow.states[-1]}"]


def test_the_pkce_verifier_is_never_sent_to_the_browser(gmail_client):
    client, flow = gmail_client
    body = client.post("/api/gmail/connect").text
    assert "verifier-for-" not in body
