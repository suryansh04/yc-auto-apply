"""Task 12: the real `EmailVerifier` adapter (spec §9, D30) —
`MyEmailVerifierClient` against MyEmailVerifier's single-address REST API.

All HTTP is mocked via `respx` (no real network, per the task-12 brief).
The endpoint verified live 2026-09-04:
`GET https://client.myemailverifier.com/verifier/validate_single/{email}/{apikey}`,
returning a JSON body with `Status` (`Valid`/`Invalid`/`Catch-all`/`Unknown`)
plus a `catch_all` flag, and HTTP 401 + `{"status":false,"message":"Invalid
API Key"}` for a bad key.

Deviation from the task-12 brief, disclosed: the brief names Reoon
throughout; per the task's own instructions the provider changed to
MyEmailVerifier after the brief was written (Reoon's service is down), so
every reference below is to MyEmailVerifier, matching `app.config.Settings
.email_verifier_api_key` (D30) and `app.adapters.email_verifier
.MyEmailVerifierClient`.

Quota-exhaustion assumption, disclosed: MyEmailVerifier's docs do not pin
the exact response shape for "no credits remain" on the single-verification
endpoint. Per the brief's fallback instruction, `QuotaExhausted` is raised
for HTTP 402/403 *and* for any response whose raw body text mentions
"credit" or "quota" (case-insensitive), regardless of status code — some
providers signal exhaustion with HTTP 200 and a message body rather than an
error status.
"""

import httpx
import pytest
import respx

from app.adapters.email_verifier import MyEmailVerifierClient
from app.ports import QuotaExhausted

API_KEY = "super-secret-key"
_URL_PATTERN = "https://client.myemailverifier.com/verifier/validate_single/a%40b.com/super-secret-key"


def _client(**kwargs) -> MyEmailVerifierClient:
    kwargs.setdefault("_sleep", lambda seconds: None)
    return MyEmailVerifierClient(API_KEY, **kwargs)


@respx.mock
def test_valid_status_maps_to_valid_verdict():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Valid", "catch_all": "false"})
    )
    assert _client().verify("a@b.com") == "valid"


@respx.mock
def test_invalid_status_maps_to_invalid_verdict():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Invalid", "catch_all": "false"})
    )
    assert _client().verify("a@b.com") == "invalid"


@respx.mock
def test_catch_all_status_maps_to_catch_all_verdict():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Catch-all", "catch_all": "true"})
    )
    assert _client().verify("a@b.com") == "catch_all"


@respx.mock
def test_catch_all_boolean_flag_overrides_a_valid_status():
    # Documented explicitly by the task-12 brief: "also check the
    # boolean-ish catch_all field" — a domain can be flagged catch-all even
    # when Status itself reads "Valid".
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Valid", "catch_all": "true"})
    )
    assert _client().verify("a@b.com") == "catch_all"


@respx.mock
def test_unrecognised_status_maps_to_unknown_not_a_guess():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Weird", "catch_all": "false"})
    )
    assert _client().verify("a@b.com") == "unknown"


@respx.mock
def test_status_mapping_is_case_insensitive():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "VALID", "catch_all": "false"})
    )
    assert _client().verify("a@b.com") == "valid"


@respx.mock
def test_missing_status_field_maps_to_unknown():
    respx.get(_URL_PATTERN).mock(return_value=httpx.Response(200, json={}))
    assert _client().verify("a@b.com") == "unknown"


@respx.mock
def test_invalid_api_key_raises_an_http_error_not_quota_exhausted():
    # Verified live 2026-09-04 per the brief: a bad key yields HTTP 401
    # with {"status": false, "message": "Invalid API Key"} — a config
    # error, not a quota signal.
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(401, json={"status": False, "message": "Invalid API Key"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        _client().verify("a@b.com")


@respx.mock
def test_http_402_raises_quota_exhausted():
    respx.get(_URL_PATTERN).mock(return_value=httpx.Response(402, text="Payment required"))
    with pytest.raises(QuotaExhausted):
        _client().verify("a@b.com")


@respx.mock
def test_http_403_raises_quota_exhausted():
    respx.get(_URL_PATTERN).mock(return_value=httpx.Response(403, text="Forbidden"))
    with pytest.raises(QuotaExhausted):
        _client().verify("a@b.com")


@respx.mock
def test_200_response_body_mentioning_credits_raises_quota_exhausted():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"message": "You have run out of credits"})
    )
    with pytest.raises(QuotaExhausted):
        _client().verify("a@b.com")


@respx.mock
def test_200_response_body_mentioning_quota_raises_quota_exhausted():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"message": "Daily quota exceeded"})
    )
    with pytest.raises(QuotaExhausted):
        _client().verify("a@b.com")


@respx.mock
def test_remaining_quota_is_zero_after_quota_exhausted_is_raised():
    respx.get(_URL_PATTERN).mock(return_value=httpx.Response(402, text="Payment required"))
    client = _client()
    with pytest.raises(QuotaExhausted):
        client.verify("a@b.com")
    assert client.remaining_quota() == 0


@respx.mock
def test_remaining_quota_is_none_before_any_exhaustion_is_seen():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Valid", "catch_all": "false"})
    )
    client = _client()
    client.verify("a@b.com")
    assert client.remaining_quota() is None


@respx.mock
def test_server_error_raises_http_status_error():
    respx.get(_URL_PATTERN).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        _client().verify("a@b.com")


# --- Rate limiting: 30 requests/minute (spec §9/D30) -> a 2s minimum
# interval between calls. Same injectable `_now`/`_sleep` throttle pattern
# as `app.adapters.yc_page.YCPageSource` (Task 11), tested the same way: a
# fake clock/sleep pair, no real waiting.

@respx.mock
def test_throttle_waits_for_the_remaining_interval_since_the_last_request():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Valid", "catch_all": "false"})
    )
    clock = {"t": 100.0}
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        clock["t"] += seconds

    client = MyEmailVerifierClient(API_KEY, _now=lambda: clock["t"], _sleep=fake_sleep)
    client._last_request_at = 100.0
    clock["t"] = 100.5  # only 0.5s elapsed since the last request

    client.verify("a@b.com")

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(1.5)  # 2.0s min interval - 0.5s elapsed


@respx.mock
def test_throttle_does_not_wait_before_the_first_request():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Valid", "catch_all": "false"})
    )
    sleep_calls = []
    client = MyEmailVerifierClient(API_KEY, _sleep=lambda s: sleep_calls.append(s))

    client.verify("a@b.com")

    assert sleep_calls == []


# --- The URL embeds the API key in the path; it must never be logged
# (task-12 brief, D30). Only the address and the resulting verdict may be
# logged.

@respx.mock
def test_the_api_key_never_appears_in_a_log_message(caplog):
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Valid", "catch_all": "false"})
    )
    with caplog.at_level("DEBUG"):
        _client().verify("a@b.com")

    for record in caplog.records:
        assert API_KEY not in record.getMessage()
