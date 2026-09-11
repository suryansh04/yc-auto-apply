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

Quota-exhaustion assumption, disclosed: the docs do not pin an exact
response shape for "no credits remain" on this endpoint. Per the brief's
own fallback instruction, `QuotaExhausted` (`app.ports.QuotaExhausted` —
raised from here, not defined here, so `ResolveEmails` in application/ can
catch it without importing this adapter, D8) is raised whenever the HTTP
status is 402 or 403, *or* the raw response body text mentions "credit" or
"quota" (case-insensitive) regardless of status code, since some providers
signal exhaustion with a 200 and a message body rather than an error
status. This is a documented guess, not a verified provider contract.

Rate limit: 30 single-verification requests/minute -> a 2-second minimum
interval between calls, self-throttled the same way as
`app.adapters.yc_page.YCPageSource` (Task 11): an injectable `_now`/`_sleep`
pair (defaulting to `time.monotonic`/`time.sleep`) so tests can pin the
throttle's behaviour without a real sleep.

The URL embeds the API key in its path, so it must never be logged (the
task-12 brief is explicit about this) — only the address and the resulting
verdict are logged, at INFO.
"""

import logging
import time

import httpx

from app.ports import QuotaExhausted

logger = logging.getLogger(__name__)

# httpx's own client logs "HTTP Request: GET <full url> ..." at INFO by
# default, which would leak the API key embedded in the URL path straight
# into the log stream regardless of anything this module logs itself.
# Raising its level is the only way to keep that promise, since the leak
# happens inside httpx's request path, not this adapter's.
logging.getLogger("httpx").setLevel(logging.WARNING)

_VALIDATE_URL = "https://client.myemailverifier.com/verifier/validate_single/{address}/{api_key}"
_RATE_LIMIT_PER_MINUTE = 30
_MIN_INTERVAL_SECONDS = 60.0 / _RATE_LIMIT_PER_MINUTE  # 2.0s
_QUOTA_STATUS_CODES = {402, 403}
_QUOTA_KEYWORDS = ("credit", "quota")

_VALID_VERDICTS = {"valid", "invalid"}
_TRUTHY = {"true", "1", "yes"}


def _is_catch_all_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY


def _looks_like_quota_exhaustion(response: httpx.Response) -> bool:
    if response.status_code in _QUOTA_STATUS_CODES:
        return True
    body_text = (response.text or "").lower()
    return any(keyword in body_text for keyword in _QUOTA_KEYWORDS)


class MyEmailVerifierClient:
    def __init__(
        self,
        api_key: str,
        timeout: float = 30,
        min_interval: float = _MIN_INTERVAL_SECONDS,
        _now=time.monotonic,
        _sleep=time.sleep,
    ):
        self._api_key = api_key
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request_at: float | None = None
        self._now = _now
        self._sleep = _sleep
        self._remaining_quota: int | None = None

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
        response = httpx.get(url, timeout=self._timeout)
        self._last_request_at = self._now()

        if _looks_like_quota_exhaustion(response):
            self._remaining_quota = 0
            logger.info("verify %s -> quota_exhausted (status %s)", address, response.status_code)
            raise QuotaExhausted(
                f"no MyEmailVerifier credits remaining (HTTP {response.status_code})"
            )

        response.raise_for_status()

        verdict = self._map_verdict(response.json())
        logger.info("verify %s -> %s", address, verdict)
        return verdict

    def _map_verdict(self, data: dict) -> str:
        status = str(data.get("Status", "")).strip().lower()
        if status == "catch-all" or _is_catch_all_flag(data.get("catch_all")):
            return "catch_all"
        if status in _VALID_VERDICTS:
            return status
        return "unknown"

    def remaining_quota(self) -> int | None:
        return self._remaining_quota
