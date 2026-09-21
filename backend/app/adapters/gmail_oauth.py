"""The Google consent flow, driven from the browser instead of the CLI.

`cli.py gmail-auth` runs `InstalledAppFlow.run_local_server`, which spins
up its own throwaway web server and blocks the terminal until Google
redirects to it. The UI cannot do that: the API is already a server, so
it owns the redirect itself and the operator never leaves the app.

Two calls, both of which this adapter is the only place to make:

  `authorization_url(state)`  -> where to send the browser
  `exchange_code(code)`       -> the refresh token to store

The client secret stays here and in the client-secrets file. It is never
returned, logged, or put in an error message, and neither is the refresh
token this hands back - the router stores it straight into
`gmail_credentials` and returns nothing about it.

The redirect URI is a loopback address on the API's own port, which a
Google "Desktop app" OAuth client accepts without any redirect URI being
registered up front, so connecting from the UI needs no change in Google
Cloud for an operator who already ran `gmail-auth`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Widened in Task 7 (spec §5): a send-only token cannot read replies, and
# Google enforces scopes on the token itself. Kept identical to
# `cli.GMAIL_SCOPES` - a token minted here must work for both jobs.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


@dataclass(frozen=True)
class GmailTokens:
    """What the router needs to persist. Deliberately not the whole
    credentials object: no access token, no client secret, no id token."""

    refresh_token: str
    token_uri: str
    scopes: list[str]


class NoRefreshToken(RuntimeError):
    """Google completed the consent but returned no refresh token, which
    happens when the account has already granted this client and Google
    sees no reason to mint a second one. The fix is the operator's:
    revoke at myaccount.google.com/permissions and connect again. Carries
    no token or code in its message."""


class GoogleOAuthFlow:
    """Real implementation over `google_auth_oauthlib`. Constructed per
    request from the client-secrets file, so rotating that file takes
    effect without a restart."""

    def __init__(self, client_secrets_path: str, redirect_uri: str, scopes: list[str] | None = None):
        self._client_secrets_path = client_secrets_path
        self._redirect_uri = redirect_uri
        self._scopes = list(scopes or GMAIL_SCOPES)

    def _flow(self):
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_secrets_file(self._client_secrets_path, scopes=self._scopes)
        flow.redirect_uri = self._redirect_uri
        return flow

    def authorization_url(self, state: str) -> str:
        url, _ = self._flow().authorization_url(
            # `offline` is what makes Google mint a refresh token at all;
            # `consent` forces the screen even when the account already
            # granted these scopes, which is the only way to get a second
            # refresh token after the first one was issued.
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
            state=state,
        )
        return url

    def exchange_code(self, code: str) -> GmailTokens:
        flow = self._flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds.refresh_token:
            raise NoRefreshToken(
                "Google returned no refresh token for this account. Revoke this "
                "app at myaccount.google.com/permissions, then connect again."
            )
        return GmailTokens(
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri or "https://oauth2.googleapis.com/token",
            scopes=list(creds.scopes or self._scopes),
        )
