"""Reading founder replies (spec §5): Gmail's History and Messages REST
APIs, over the same OAuth account `GmailSender` sends from.

Why History, not a plain inbox scan: `users.history.list` returns only
what changed since a cursor (`startHistoryId`), so a poller can ask "what's
new since last time" in one cheap call instead of re-scanning the mailbox.
`recent_message_ids` (a plain `users.messages.list` with a `newer_than:`
query) exists for the one time History can't answer that: a fresh account
with no stored cursor yet, or a cursor Gmail has expired (see
`GmailHistoryExpired` below) - Task 8 falls back to it and then calls
`current_history_id()` to establish a fresh cursor to resume from next
time.

API shapes - disclosed guesses, verified against Google's public Gmail API
docs but NOT against a live call from this machine (no OAuth client here):

- History: `GET /users/me/history?startHistoryId=<id>&historyTypes=
  messageAdded[&pageToken=<token>]` -> `{"history": [{"messagesAdded":
  [{"message": {"id", "threadId"}}, ...]}, ...], "historyId": <new
  cursor>, "nextPageToken": <optional>}`. A `startHistoryId` older than
  what Gmail retains (documented as roughly a week, not guaranteed)
  answers HTTP 404 - that is `GmailHistoryExpired`, not a `GmailError`,
  so the caller can tell "go do a full resync" apart from "something
  actually broke."
- List: `GET /users/me/messages?q=<query>[&pageToken=<token>]` ->
  `{"messages": [{"id", "threadId"}, ...], "nextPageToken": <optional>}`.
- Get: `GET /users/me/messages/<id>?format=full` -> `{"id", "threadId",
  "internalDate": <ms epoch string>, "payload": {"headers": [{"name",
  "value"}, ...], "mimeType", "body": {"data": <base64url>},
  "parts": [...]}}`. Gmail API `body.data` is the MIME part's content
  already decoded from whatever Content-Transfer-Encoding the raw email
  used (base64, quoted-printable, ...) and re-encoded as base64url for
  JSON transport - so a base64url decode is the only decoding this
  adapter has to do, even for a quoted-printable founder reply with the
  original email quoted below it. This adapter extracts the text/plain
  part faithfully, quoting and all; deciding what to do with the quoted
  tail is Task 8's job.
- Profile: `GET /users/me/profile` -> `{"historyId": <current cursor>,
  ...}`, used by `current_history_id()` to establish a cursor after a
  fallback scan.

Token exchange is NOT duplicated here: `exchange_refresh_token` in
`app.adapters.gmail` is the one place the `POST /token` call is written
(shared with `GmailSender`), following this project's rule against
duplicating shared wiring.

Secrets never leak: same discipline as `GmailSender` - the refresh token,
client secret and access token are never logged and never placed in an
exception message. `GmailError` carries only a stage label and HTTP
status; the reader does not retry a 401 in-request (unlike `GmailSender`,
whose retry matters for a user-facing send) - a poll that hits a stale
token simply fails this run and the *next* scheduled run refreshes
naturally, which keeps this adapter simple and avoids a second,
observable network call on every auth failure.
"""

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Callable

import httpx

from app.adapters.gmail import GmailError, _error_code, exchange_refresh_token

logger = logging.getLogger(__name__)

HISTORY_URL = "https://gmail.googleapis.com/gmail/v1/users/me/history"
MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

_EXPIRY_MARGIN_SECONDS = 60.0  # same margin as GmailSender: refresh this long before Google says it dies
_TIMEOUT_SECONDS = 30.0


class GmailHistoryExpired(Exception):
    """Gmail answered 404 to `users.history.list`: `startHistoryId` is
    older than what the mailbox retains. Not a `GmailError` - this is an
    expected, recoverable condition (the caller falls back to
    `recent_message_ids` + `current_history_id()`), not an adapter
    failure."""


@dataclass(frozen=True)
class InboundMessage:
    """One Gmail message as `get_message` resolves it. Field names match
    the brief's interface verbatim - Task 8 maps these onto message rows."""

    gmail_message_id: str
    gmail_thread_id: str
    from_address: str
    subject: str
    body_text: str
    received_at: datetime


class GmailReader:
    """Reads replies via Gmail's `history` and `messages` endpoints (spec
    §5).

    `credential_repo` + `account` rather than a raw refresh token: the
    stored row is re-read from `credential_repo` every time the cached
    access token needs renewing, so a refresh token rotated elsewhere
    (re-running `gmail-auth`) is always picked up rather than one copied
    in at construction going stale.

    `client_id` / `client_secret` are required constructor arguments
    (not sourced from `credential_repo`): `GmailCredential` deliberately
    does not store them (see its docstring in `app.adapters.db.models` -
    the OAuth client identity lives in the client-secrets JSON, not the
    database), so they are loaded once by the caller - exactly how
    `app.cli.send` already loads them via `_load_oauth_client` and passes
    them into `GmailSender` explicitly - and passed in here the same way.

    `_sleep` and `_client` are injection points for tests, following the
    same convention as `GmailSender`'s `http`/`now` parameters.
    """

    def __init__(
        self,
        credential_repo,
        account: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 30,
        _sleep: Callable[[float], None] = time.sleep,
        _client: httpx.Client | None = None,
    ):
        self._credential_repo = credential_repo
        self._account = account
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = _client or httpx.Client(timeout=timeout)
        self._sleep = _sleep
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def __repr__(self) -> str:
        return f"GmailReader(account={self._account!r})"

    def close(self) -> None:
        self._http.close()

    # -- reads -----------------------------------------------------------

    def history_since(self, history_id: str) -> tuple[list[str], str]:
        """New message ids added since `history_id`, and the mailbox's
        current history id to resume from next time. Pages through
        `nextPageToken` - a founder replying while the last poll was
        running can easily span pages, and stopping at the first page
        would silently lose replies. Raises `GmailHistoryExpired` on a
        404 (cursor too old for Gmail to answer)."""
        ids: list[str] = []
        cursor = str(history_id)
        page_token: str | None = None
        while True:
            params: dict[str, str] = {"startHistoryId": str(history_id), "historyTypes": "messageAdded"}
            if page_token:
                params["pageToken"] = page_token
            response = self._get(HISTORY_URL, params, stage="history")
            if response.status_code == 404:
                raise GmailHistoryExpired(f"startHistoryId {history_id!r} is no longer retained by Gmail")
            self._raise_for_status("history", response)
            payload = response.json()
            for entry in payload.get("history") or []:
                for added in entry.get("messagesAdded") or []:
                    message_id = (added.get("message") or {}).get("id")
                    if message_id:
                        ids.append(message_id)
            if payload.get("historyId"):
                cursor = str(payload["historyId"])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return ids, cursor

    def recent_message_ids(self, query: str = "newer_than:14d") -> list[str]:
        """Message ids matching a Gmail search query - the fallback path
        when there is no usable history cursor yet. Pages through
        `nextPageToken` for the same reason `history_since` does."""
        ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {"q": query}
            if page_token:
                params["pageToken"] = page_token
            response = self._get(MESSAGES_URL, params, stage="list messages")
            self._raise_for_status("list messages", response)
            payload = response.json()
            ids.extend(m["id"] for m in (payload.get("messages") or []) if m.get("id"))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return ids

    def get_message(self, message_id: str) -> InboundMessage:
        """The full message: headers, decoded plain-text body, and the
        received timestamp. Prefers the `text/plain` MIME part over any
        `text/html` alternative (D10's plain-text preference, applied to
        reading as well as sending)."""
        response = self._get(f"{MESSAGES_URL}/{message_id}", {"format": "full"}, stage="get message")
        self._raise_for_status("get message", response)
        payload = response.json()
        root = payload.get("payload") or {}
        headers = {h.get("name"): h.get("value") for h in (root.get("headers") or [])}
        _, from_address = parseaddr(headers.get("From") or "")
        subject = headers.get("Subject") or ""
        body_text = _extract_body_text(root)
        received_at = datetime.fromtimestamp(int(payload.get("internalDate", 0)) / 1000, tz=timezone.utc)
        return InboundMessage(
            gmail_message_id=str(payload.get("id", message_id)),
            gmail_thread_id=str(payload.get("threadId", "")),
            from_address=from_address,
            subject=subject,
            body_text=body_text,
            received_at=received_at,
        )

    def current_history_id(self) -> str:
        """The mailbox's current history id, e.g. to establish a fresh
        cursor after a `recent_message_ids` fallback scan."""
        response = self._get(PROFILE_URL, None, stage="profile")
        self._raise_for_status("profile", response)
        return str(response.json()["historyId"])

    # -- internals ---------------------------------------------------------

    def _get(self, url: str, params: dict[str, str] | None, *, stage: str) -> httpx.Response:
        token = self._access_token_value()
        try:
            return self._http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise GmailError(stage, reason=type(exc).__name__) from None

    def _raise_for_status(self, stage: str, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise GmailError(stage, status=response.status_code, reason=_error_code(response))

    def _access_token_value(self) -> str:
        if self._access_token is None or time.monotonic() >= self._expires_at - _EXPIRY_MARGIN_SECONDS:
            self._refresh()
        assert self._access_token is not None
        return self._access_token

    def _refresh(self) -> None:
        credential = self._credential_repo.get_by_account_email(self._account)
        if credential is None or not getattr(credential, "refresh_token", None):
            raise GmailError("token refresh", reason="no stored credential")
        self._access_token, expires_in = exchange_refresh_token(
            self._http, self._client_id, self._client_secret, credential.refresh_token,
        )
        self._expires_at = time.monotonic() + expires_in
        logger.info("gmail reader access token refreshed; expires in %.0fs", expires_in)


def _decode_body(data: str) -> str:
    """Gmail's `body.data` is base64url, sometimes without padding."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _iter_leaf_parts(payload: dict):
    """Depth-first walk to the leaf MIME parts (no further `parts`) -
    handles both a single-part message and an arbitrarily nested
    multipart one (e.g. `multipart/mixed` wrapping `multipart/
    alternative`)."""
    parts = payload.get("parts")
    if parts:
        for part in parts:
            yield from _iter_leaf_parts(part)
    else:
        yield payload


def _extract_body_text(payload: dict) -> str:
    """The first `text/plain` leaf part's decoded content, or `""` when
    the message has none (e.g. an image-only or html-only reply)."""
    for leaf in _iter_leaf_parts(payload):
        if leaf.get("mimeType") == "text/plain":
            data = (leaf.get("body") or {}).get("data")
            if data:
                return _decode_body(data)
    return ""
