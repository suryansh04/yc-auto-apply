"""Connecting Gmail from the browser.

Before this, authorising Gmail meant leaving the app, running
`python -m app.cli gmail-auth` in a terminal, and coming back. The UI now
gates itself on `GET /api/gmail/status` and walks the operator through
the consent screen, so the dashboard only appears once the app can
actually send and read mail.

Three endpoints:

  GET  /api/gmail/status    what the UI gates on
  POST /api/gmail/connect   mint a one-shot state, return the consent URL
  GET  /api/gmail/callback  Google sends the browser back here

What never crosses this boundary: the client secret, the refresh token,
the authorisation code. `status` reports whether a token exists and which
account it belongs to - both already known to the operator - and nothing
else. A failure returns the exception's type name, never its message,
for the same reason job rows do (spec §9).
"""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.adapters.gmail_oauth import GMAIL_SCOPES, NoRefreshToken
from app.api.deps import Repos, get_repos, require_gmail_config
from app.api.schemas import GmailConnectOut, GmailStatusOut
from app.config import get_operator_settings

router = APIRouter(tags=["gmail"])

# A consent round trip that has not come back within this window is dead:
# the operator closed the tab, or a stale link was reopened hours later.
STATE_TTL_SECONDS = 600


def _oauth_flow(request: Request, settings):
    """The Google flow this app was built with. `create_app` puts a
    factory on `app.state` (the same seam the job runner uses), so the
    offline suite can exercise every branch here without a browser, a
    client-secrets file or a network."""
    factory = request.app.state.gmail_oauth_factory
    return factory(settings, str(request.url_for("gmail_callback")))


def _credential(repos: Repos, account_email: str):
    if not account_email:
        return None
    return repos.credential.get_by_account_email(account_email)


@router.get("/gmail/status", response_model=GmailStatusOut)
def gmail_status(repos: Repos = Depends(get_repos)) -> GmailStatusOut:
    settings = get_operator_settings()
    missing = require_gmail_config(settings)
    credential = _credential(repos, settings.gmail_sender_address)
    scopes = list(getattr(credential, "scopes", None) or []) if credential else []
    return GmailStatusOut(
        configured=not missing,
        missing_config=missing,
        connected=credential is not None,
        account=settings.gmail_sender_address or None,
        # A token minted before Task 7 can send but cannot read replies.
        # It looks connected and fails only when the reply poller runs, so
        # the UI needs to know to offer a reconnect.
        can_read_replies=all(scope in scopes for scope in GMAIL_SCOPES),
    )


@router.post("/gmail/connect", response_model=GmailConnectOut)
def gmail_connect(request: Request) -> GmailConnectOut:
    settings = get_operator_settings()
    missing = require_gmail_config(settings)
    if missing:
        raise HTTPException(409, f"Gmail is not configured yet: {', '.join(missing)}")

    # One-shot, server-side state: the callback is an unauthenticated GET
    # that anything on this machine could hit, so a code only counts if
    # it comes back with a state this process minted and has not spent.
    state = secrets.token_urlsafe(32)
    request.app.state.gmail_oauth_states[state] = time.monotonic()
    return GmailConnectOut(auth_url=_oauth_flow(request, settings).authorization_url(state))


@router.get("/gmail/callback", name="gmail_callback")
def gmail_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    repos: Repos = Depends(get_repos),
):
    """Where Google sends the browser. Always ends in a redirect back to
    the app - this page is a hop, not a screen - with the outcome in the
    query string so the UI can say what happened."""
    app_url = get_operator_settings().app_url.rstrip("/")

    def back(outcome: str):
        return RedirectResponse(f"{app_url}/?gmail={outcome}", status_code=303)

    # The operator pressed "Cancel" on Google's screen.
    if error:
        return back("cancelled")

    issued_at = request.app.state.gmail_oauth_states.pop(state, None) if state else None
    if issued_at is None or (time.monotonic() - issued_at) > STATE_TTL_SECONDS:
        return back("expired")
    if not code:
        return back("failed")

    settings = get_operator_settings()
    try:
        tokens = _oauth_flow(request, settings).exchange_code(code)
    except NoRefreshToken:
        return back("no-refresh-token")
    except Exception:  # noqa: BLE001 - the browser gets an outcome, the log gets the trace
        return back("failed")

    repos.credential.upsert(
        account_email=settings.gmail_sender_address,
        refresh_token=tokens.refresh_token,
        token_uri=tokens.token_uri,
        scopes=tokens.scopes,
    )
    repos.session.commit()
    return back("connected")
