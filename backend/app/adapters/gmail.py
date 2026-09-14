"""Real `MailSender` adapter (spec §13, D4, D10, spec §18): Gmail's REST
API, `users.messages.send`, over the user's own account via OAuth.

Why plain `httpx` and not `google-api-python-client`: the send call is a
single JSON POST with a Bearer token, and the token refresh is a single
form POST. Two `httpx` calls are trivially mockable offline (`respx`) and
carry no discovery-document machinery; the Google client library is still
a dependency, but only `gmail-auth` (the one-time OAuth bootstrap in
`app.cli`) uses it, via `google-auth-oauthlib`'s `InstalledAppFlow`.

API shapes — disclosed guesses, verified against Google's public docs but
NOT against a live call from this machine (no OAuth client here):

- Token refresh: `POST https://oauth2.googleapis.com/token` with form
  fields `client_id`, `client_secret`, `refresh_token`,
  `grant_type=refresh_token` -> `{"access_token", "expires_in", ...}`.
  A revoked/expired refresh token answers HTTP 400 `{"error":
  "invalid_grant", ...}`; the operator's fix is to re-run `gmail-auth`.
- Send: `POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send`
  with JSON `{"raw": <base64url of the RFC 2822 message>}` and
  `Authorization: Bearer <access token>` -> `{"id", "threadId", ...}`.

Secrets never leak: the refresh token, client secret and access token are
held on the instance (excluded from `__repr__`), never logged, and never
placed in an exception message — `GmailError` carries only the HTTP status
and a short reason (`error` code from the response body when it is a
plain string, e.g. `invalid_grant`), never the response body itself.
httpx's own INFO log line is `"HTTP Request: POST <url> ..."`; neither
endpoint URL carries a secret, so no logger filter is needed here (unlike
`app.adapters.email_verifier`, whose provider embeds the key in the path).

MIME (D10, spec §18): `text/plain` body only — no `text/html` alternative,
no tracking pixel, no link shortener — plus the resume PDF as a single
`application/pdf` attachment. `build_mime` is a pure function so the test
suite can parse its output back and assert exactly that structure.
"""

import base64
import logging
import mimetypes
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

_EXPIRY_MARGIN_SECONDS = 60.0  # refresh this long before Google says the token dies
_TIMEOUT_SECONDS = 30.0


class GmailError(Exception):
    """Any Gmail/OAuth failure. Message is built from `status` and `reason`
    only — never from a response body or an underlying exception's text —
    so it is always safe to log, print, or surface via a CLI traceback."""

    def __init__(self, stage: str, status: int | None = None, reason: str | None = None):
        self.stage = stage
        self.status = status
        self.reason = reason
        parts = [f"gmail {stage} failed"]
        if status is not None:
            parts.append(f"(HTTP {status})")
        if reason:
            parts.append(f"- {reason}")
        super().__init__(" ".join(parts))


def build_mime(sender: str, to: str, subject: str, body: str, attachment_path: str | None) -> bytes:
    """RFC 2822 bytes: a `text/plain` body and, when `attachment_path` is
    given, that file as an attachment (PDF by default; the MIME type is
    guessed from the suffix). Raises `FileNotFoundError` for a missing
    attachment — before any network call is made."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)  # text/plain; charset chosen by the stdlib (utf-8 when needed)
    if attachment_path is not None:
        path = Path(attachment_path)
        data = path.read_bytes()  # FileNotFoundError propagates
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/pdf").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg.as_bytes()


def encode_raw(mime_bytes: bytes) -> str:
    """Gmail wants the message base64url-encoded (RFC 4648 §5) in `raw`."""
    return base64.urlsafe_b64encode(mime_bytes).decode("ascii")


class GmailSender:
    """`MailSender` over Gmail. `http` and `now` are injectable for tests
    (`now` is a monotonic-seconds clock used only for token expiry)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        sender_address: str,
        http: httpx.Client | None = None,
        now: Callable[[], float] = time.monotonic,
    ):
        if not (client_id and client_secret and refresh_token and sender_address):
            raise ValueError("client_id, client_secret, refresh_token and sender_address are all required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self.sender_address = sender_address
        self._http = http or httpx.Client(timeout=_TIMEOUT_SECONDS)
        self._now = now
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def __repr__(self) -> str:
        return f"GmailSender(sender_address={self.sender_address!r})"

    def close(self) -> None:
        self._http.close()

    # -- MailSender ----------------------------------------------------------

    def send(self, to: str, subject: str, body: str, attachment_path: str | None) -> tuple[str, str]:
        raw = encode_raw(build_mime(self.sender_address, to, subject, body, attachment_path))
        response = self._post_send(raw)
        if response.status_code == 401:
            # Access token rejected despite looking fresh: refresh once, retry once.
            logger.info("gmail send got 401; refreshing the access token and retrying once")
            self._access_token = None
            response = self._post_send(raw)
        if response.status_code >= 400:
            raise GmailError("send", status=response.status_code)
        try:
            payload = response.json()
            message_id, thread_id = payload["id"], payload["threadId"]
        except (ValueError, KeyError, TypeError):
            raise GmailError("send", status=response.status_code, reason="response missing id/threadId")
        logger.info("gmail send ok: to=%s message_id=%s thread_id=%s", to, message_id, thread_id)
        return str(message_id), str(thread_id)

    # -- internals -----------------------------------------------------------

    def _post_send(self, raw: str) -> httpx.Response:
        token = self._access_token_value()
        try:
            return self._http.post(
                SEND_URL, json={"raw": raw},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise GmailError("send", reason=type(exc).__name__) from None

    def _access_token_value(self) -> str:
        if self._access_token is None or self._now() >= self._expires_at - _EXPIRY_MARGIN_SECONDS:
            self._refresh()
        assert self._access_token is not None
        return self._access_token

    def _refresh(self) -> None:
        self._access_token, expires_in = exchange_refresh_token(
            self._http, self._client_id, self._client_secret, self._refresh_token,
        )
        self._expires_at = self._now() + expires_in
        logger.info("gmail access token refreshed; expires in %.0fs", expires_in)


def exchange_refresh_token(
    http: httpx.Client, client_id: str, client_secret: str, refresh_token: str,
) -> tuple[str, float]:
    """The OAuth refresh-token exchange (`POST /token`), shared by every
    Gmail adapter that needs an access token - `GmailSender` here and
    `app.adapters.gmail_reader.GmailReader` (Task 7). Written once so the
    exchange is never duplicated, following this project's precedent for
    shared wiring (`require_send_config`, `on_gemini_free_tier`,
    `build_drafter` are each extracted to one home rather than copied).

    Returns `(access_token, expires_in_seconds)`. Raises `GmailError`
    ("token refresh", ...) on any failure - transport error, non-2xx
    response, or a malformed body - carrying only the HTTP status and a
    short OAuth error code (`_error_code`), never the refresh token, the
    client secret, or the response body."""
    try:
        response = http.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    except httpx.HTTPError as exc:
        raise GmailError("token refresh", reason=type(exc).__name__) from None
    if response.status_code >= 400:
        raise GmailError("token refresh", status=response.status_code,
                         reason=_error_code(response))
    try:
        payload = response.json()
        token = str(payload["access_token"])
        expires_in = float(payload.get("expires_in", 3600))
    except (ValueError, KeyError, TypeError):
        raise GmailError("token refresh", status=response.status_code,
                         reason="response missing access_token")
    return token, expires_in


def _error_code(response: httpx.Response) -> str | None:
    """The OAuth `error` code (`invalid_grant`, `invalid_client`, ...) when
    the body carries one as a plain string — a short enum the operator
    needs, never the free-text description or the whole body."""
    try:
        code = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and len(code) <= 40 else None
