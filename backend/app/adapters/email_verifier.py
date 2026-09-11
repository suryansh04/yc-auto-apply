"""Real `EmailVerifier` adapter (spec §9, D30): MyEmailVerifier's
single-address verification API.

Provider swap, disclosed: the task-12 brief names Reoon throughout, written
before Reoon's service went down. The provider is now MyEmailVerifier —
`app.config.Settings.email_verifier_api_key` (env `EMAIL_VERIFIER_API_KEY`)
is already named for the role rather than either vendor (D30), so the swap
touches no configuration, only this adapter.

Endpoint, verified live 2026-09-04 (per the brief):
`GET https://client.myemailverifier.com/verifier/validate_single/{email}/{apikey}`.
A successful response is JSON carrying `Status` (`Valid` | `Invalid` |
`Catch-all` | `Unknown`) plus a boolean-ish `catch_all` field (among others
this adapter does not use: `Disposable_Domain`, `Role_Based`, `Diagnosis`).
An invalid API key yields HTTP 401 with
`{"status": false, "message": "Invalid API Key"}`.

Fix round 1 — two blockers from the review of commit `c6c0e8e`:

1. **The API key must never leak.** The URL embeds it in the path, so two
   independent things used to leak it: (a) any `httpx.HTTPStatusError`/
   `httpx.HTTPError` escaping `verify` — httpx formats those exceptions
   with the full request URL, and an uncaught one reached the CLI's
   traceback or any log recording it; (b) httpx's *own* internal logger,
   which logs `"HTTP Request: GET <full url> ..."` at INFO regardless of
   anything this module does. Fix: every httpx exception is now caught
   inside `verify` and re-raised as `app.ports.VerifierError`, which
   carries only the address and HTTP status, never the URL or the
   underlying exception's own message; and a `logging.Filter` redacting
   this instance's own key is attached to the `httpx` logger in
   `__init__` (not a module-level `setLevel`, which was a global,
   silently-reversible side effect — see the class docstring).
2. **The quota heuristic false-positived on ordinary responses.** The
   original version scanned the *entire* raw response body for the
   substrings "credit"/"quota" before even trying to parse JSON — so a
   perfectly normal `{"Status": "Valid", "Address": "raj@creditkarma.com",
   ...}` response (MyEmailVerifier echoes the queried address back) was
   misread as quota exhaustion, permanently stalling every run that ever
   reached a "credit"-named domain (and YC's own batch has several).
   Fixed in `verify` below: JSON is parsed first, and *any* response
   carrying a `Status` key is mapped to a verdict directly — quota is
   never even considered once `Status` is present. Only when `Status` is
   absent does quota get checked at all, and then only via the HTTP status
   (402/403) or a `message`/`error` field specifically (never the whole
   body).

Rate limit: 30 single-verification requests/minute -> a 2-second minimum
interval between calls, self-throttled the same way as
`app.adapters.yc_page.YCPageSource` (Task 11): an injectable `_now`/`_sleep`
pair (defaulting to `time.monotonic`/`time.sleep`) so tests can pin the
throttle's behaviour without a real sleep.
"""

import logging
import time

import httpx

from app.ports import QuotaExhausted, VerifierError

logger = logging.getLogger(__name__)

_VALIDATE_URL = "https://client.myemailverifier.com/verifier/validate_single/{address}/{api_key}"
_RATE_LIMIT_PER_MINUTE = 30
_MIN_INTERVAL_SECONDS = 60.0 / _RATE_LIMIT_PER_MINUTE  # 2.0s
_QUOTA_STATUS_CODES = {402, 403}
_QUOTA_KEYWORDS = ("credit", "quota")
_QUOTA_SIGNAL_FIELDS = ("message", "error")

_VALID_VERDICTS = {"valid", "invalid"}
_TRUTHY = {"true", "1", "yes"}
_REDACTED = "***REDACTED***"


def _is_catch_all_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY


class _RedactApiKeyFilter(logging.Filter):
    """Strips this client's own API key out of any log record it appears
    in, wherever the record came from — in particular httpx's own
    request-URL log (`"HTTP Request: GET <full url> ..."` at INFO), which
    otherwise leaks the key embedded in the URL path regardless of
    anything `MyEmailVerifierClient` logs itself. Attached to the `httpx`
    logger in `__init__`.

    Deliberately a filter, not a module-level `logging.getLogger("httpx")
    .setLevel(...)` (fix round 1, minor): a `setLevel` call is a global
    side effect any later logging reconfiguration (a `dictConfig`, an
    unrelated `setLevel(DEBUG)` on the same logger) can silently
    re-enable, and it also mutes `httpx` for every *other* adapter in the
    process, not just this one. A filter degrades to a no-op (the message
    just doesn't contain the key) rather than to a leak if it's ever
    accidentally removed, and only redacts — it never suppresses log
    output other code may depend on.
    """

    def __init__(self, api_key: str):
        super().__init__()
        self._api_key = api_key

    def filter(self, record: logging.LogRecord) -> bool:
        if self._api_key and self._api_key in record.getMessage():
            record.msg = record.getMessage().replace(self._api_key, _REDACTED)
            record.args = ()
        return True


class MyEmailVerifierClient:
    def __init__(
        self,
        api_key: str,
        timeout: float = 30,
        min_interval: float = _MIN_INTERVAL_SECONDS,
        _now=time.monotonic,
        _sleep=time.sleep,
        _client: httpx.Client | None = None,
    ):
        self._api_key = api_key
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request_at: float | None = None
        self._now = _now
        self._sleep = _sleep
        self._remaining_quota: int | None = None
        # Fix round 1 (minor): one persistent client per instance, rather
        # than a fresh `httpx.get(...)` (and its own throwaway SSL
        # context/TLS handshake) per call — `_client` is injectable so
        # tests can supply their own if ever needed, though respx patches
        # the default transport either way.
        self._client = _client if _client is not None else httpx.Client()
        logging.getLogger("httpx").addFilter(_RedactApiKeyFilter(api_key))

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._now() - self._last_request_at
        remaining = self._min_interval - elapsed
        if remaining > 0:
            self._sleep(remaining)

    def verify(self, address: str) -> str:
        self._throttle()
        url = _VALIDATE_URL.format(address=address, api_key=self._api_key)

        try:
            response = self._client.get(url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            # Transport-level failure (connect error, timeout, ...): never
            # let the underlying exception's own message escape, since
            # httpx transport errors can carry the request (and its URL,
            # with the key) on `.request`.
            self._last_request_at = self._now()
            logger.info("verify %s -> transport error (%s)", address, type(exc).__name__)
            raise VerifierError(address, detail=type(exc).__name__) from None

        self._last_request_at = self._now()
        status = response.status_code

        try:
            data = response.json()
        except ValueError:
            data = None

        # Fix round 1 (blocker): a `Status` key always wins — a response
        # carrying one is a real verdict, never a quota signal, no matter
        # what its status code or any other field says (MyEmailVerifier
        # echoes the queried `Address` back, so a domain like
        # "creditkarma.com" must never be misread as "out of credits").
        if isinstance(data, dict) and "Status" in data:
            verdict = self._map_verdict(data)
            logger.info("verify %s -> %s", address, verdict)
            return verdict

        if self._signals_quota_exhaustion(status, data):
            self._remaining_quota = 0
            logger.info("verify %s -> quota_exhausted (status %s)", address, status)
            raise QuotaExhausted(f"no MyEmailVerifier credits remaining (HTTP {status})")

        logger.info("verify %s -> error (status %s)", address, status)
        raise VerifierError(address, status=status)

    def _signals_quota_exhaustion(self, status: int, data) -> bool:
        if status in _QUOTA_STATUS_CODES:
            return True
        if not isinstance(data, dict):
            return False
        text = " ".join(str(data.get(field, "")) for field in _QUOTA_SIGNAL_FIELDS).lower()
        return any(keyword in text for keyword in _QUOTA_KEYWORDS)

    def _map_verdict(self, data: dict) -> str:
        status = str(data.get("Status", "")).strip().lower()
        if status == "catch-all" or _is_catch_all_flag(data.get("catch_all")):
            return "catch_all"
        if status in _VALID_VERDICTS:
            return status
        return "unknown"

    def remaining_quota(self) -> int | None:
        return self._remaining_quota
