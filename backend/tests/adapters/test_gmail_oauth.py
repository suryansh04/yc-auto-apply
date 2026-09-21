"""The scope-order relaxation that made connecting from the UI work."""
import os

from app.adapters.gmail_oauth import GMAIL_SCOPES, GoogleOAuthFlow, MissingScope, _relaxed_scope_order


def test_relaxation_is_scoped_to_the_exchange_and_restores_the_environment():
    os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
    with _relaxed_scope_order():
        assert os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "1"
    assert "OAUTHLIB_RELAX_TOKEN_SCOPE" not in os.environ


def test_relaxation_restores_an_operators_own_setting():
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "operator-set"
    try:
        with _relaxed_scope_order():
            pass
        assert os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "operator-set"
    finally:
        os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)


class _Creds:
    def __init__(self, scopes):
        self.scopes = scopes
        self.refresh_token = "r"
        self.token_uri = "https://oauth2.googleapis.com/token"


class _Flow:
    def __init__(self, granted):
        self.credentials = _Creds(granted)

    def fetch_token(self, code):
        pass


def _flow_granting(granted, monkeypatch):
    oauth = GoogleOAuthFlow("unused.json", "http://127.0.0.1:8000/api/gmail/callback")
    monkeypatch.setattr(oauth, "_flow", lambda: _Flow(granted))
    return oauth


def test_scopes_returned_in_a_different_order_are_accepted(monkeypatch):
    # Exactly what Google does: we ask send-then-readonly, it grants both
    # and answers readonly-then-send.
    oauth = _flow_granting(list(reversed(GMAIL_SCOPES)), monkeypatch)
    assert set(oauth.exchange_code("code").scopes) == set(GMAIL_SCOPES)


def test_a_missing_scope_is_rejected_even_though_ordering_is_relaxed(monkeypatch):
    oauth = _flow_granting(["https://www.googleapis.com/auth/gmail.send"], monkeypatch)
    try:
        oauth.exchange_code("code")
    except MissingScope as exc:
        assert "readonly" in str(exc)
    else:
        raise AssertionError("a consent missing the read scope must not be accepted")
