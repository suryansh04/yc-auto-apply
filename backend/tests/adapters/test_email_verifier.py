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
for HTTP 402/403, or for a `message`/`error` field (only once a response
has parsed as JSON and carries no `Status` key) mentioning "credit" or
"quota" case-insensitively.

Fix round 1 (review of commit `c6c0e8e`) added:
- every test that used to expect `httpx.HTTPStatusError`/rely on it now
  expects `app.ports.VerifierError`, which never carries the request URL
  (and therefore never the embedded API key) — see the two blockers below;
- explicit key-leak tests: a 401's exception message, a transport-error
  (`httpx.ConnectError`) exception message, and the 401 path through
  `caplog`, none of which may contain the API key;
- a quota-heuristic regression test: a normal `Status: Valid` response
  whose echoed `Address` field contains the substring "credit" must still
  verify as `"valid"`, not be misread as quota exhaustion;
- a non-JSON 200 body must raise `VerifierError`, never propagate a raw
  `json.JSONDecodeError`;
- a persistent-`httpx.Client` regression test (the adapter used to open a
  fresh client, and its own TLS context, on every single call).
"""

import httpx
import pytest
import respx

from app.adapters.email_verifier import MyEmailVerifierClient
from app.ports import QuotaExhausted, VerifierError

API_KEY = "super-secret-key"
# Fix round 1 (minor): the literal address httpx actually sends is
# "a@b.com" — respx normalises either way, but the previous "%40"-encoded
# literal here was misleading about what's on the wire.
_URL_PATTERN = "https://client.myemailverifier.com/verifier/validate_single/a@b.com/super-secret-key"


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
    # The brief itself says only "Map `Status` case-insensitively"; the
    # additional "also check the boolean-ish catch_all field" rule below
    # is this adapter's own conservative choice, not a brief requirement
    # (fix round 1: the previous version of this comment misattributed it
    # to the brief). A domain can be flagged catch-all even when Status
    # itself reads "Valid", so a "valid" verdict there would still be
    # meaningless — checking the flag too avoids ever surfacing that as a
    # guess.
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


# --- Fix round 1: quota heuristic must never fire once a `Status` key is
# present, no matter what any other field in the same body says — the
# original heuristic scanned the *whole* raw body for "credit"/"quota"
# before parsing JSON, so a real `Status: Valid` response echoing back an
# address like "raj@creditkarma.com" was misread as quota exhaustion and
# permanently stalled every run that ever reached such a domain.

@respx.mock
def test_status_field_wins_over_a_credit_flavoured_address_regression():
    respx.get(
        "https://client.myemailverifier.com/verifier/validate_single/raj@creditkarma.com/super-secret-key"
    ).mock(
        return_value=httpx.Response(
            200, json={"Status": "Valid", "Address": "raj@creditkarma.com", "catch_all": "false"}
        )
    )
    assert _client().verify("raj@creditkarma.com") == "valid"


@respx.mock
def test_missing_status_field_raises_verifier_error_not_a_guessed_verdict():
    # No `Status` key at all (as opposed to an unrecognised *value*, which
    # `_map_verdict` maps to "unknown") means this adapter has nothing to
    # go on — that's an error, not a silent "unknown" verdict that would
    # get persisted and never re-checked.
    respx.get(_URL_PATTERN).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(VerifierError):
        _client().verify("a@b.com")


@respx.mock
def test_non_json_200_body_raises_verifier_error_not_a_json_decode_error():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, text="<html>we are down for maintenance</html>")
    )
    with pytest.raises(VerifierError):
        _client().verify("a@b.com")


# --- Fix round 1: the API key must never escape `verify`, in any form —
# not in an exception's own message, and not in a log record, regardless
# of which path failure took.

@respx.mock
def test_invalid_api_key_raises_verifier_error_not_quota_exhausted():
    # Verified live 2026-09-04 per the brief: a bad key yields HTTP 401
    # with {"status": false, "message": "Invalid API Key"} — a config
    # error, not a quota signal.
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(401, json={"status": False, "message": "Invalid API Key"})
    )
    with pytest.raises(VerifierError) as exc_info:
        _client().verify("a@b.com")
    assert API_KEY not in str(exc_info.value)
    assert exc_info.value.status == 401


@respx.mock
def test_invalid_api_key_401_does_not_log_the_key(caplog):
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(401, json={"status": False, "message": "Invalid API Key"})
    )
    with caplog.at_level("DEBUG"):
        with pytest.raises(VerifierError):
            _client().verify("a@b.com")

    for record in caplog.records:
        assert API_KEY not in record.getMessage()


@respx.mock
def test_transport_error_raises_verifier_error_without_the_key():
    respx.get(_URL_PATTERN).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(VerifierError) as exc_info:
        _client().verify("a@b.com")
    assert API_KEY not in str(exc_info.value)


@respx.mock
def test_server_error_raises_verifier_error():
    respx.get(_URL_PATTERN).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(VerifierError) as exc_info:
        _client().verify("a@b.com")
    assert exc_info.value.status == 500
    assert API_KEY not in str(exc_info.value)


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
def test_200_response_message_field_mentioning_credits_raises_quota_exhausted():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"message": "You have run out of credits"})
    )
    with pytest.raises(QuotaExhausted):
        _client().verify("a@b.com")


@respx.mock
def test_200_response_error_field_mentioning_quota_raises_quota_exhausted():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"error": "Daily quota exceeded"})
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


# --- Fix round 1 (minor): one persistent httpx.Client per adapter
# instance, not a fresh client (and TLS context) per call.

@respx.mock
def test_reuses_one_http_client_across_calls():
    respx.get(_URL_PATTERN).mock(
        return_value=httpx.Response(200, json={"Status": "Valid", "catch_all": "false"})
    )
    client = _client()
    held_client = client._client

    client.verify("a@b.com")
    client.verify("a@b.com")

    assert client._client is held_client


@respx.mock
def test_close_closes_the_held_http_client():
    client = _client()
    client.close()
    assert client._client.is_closed
