"""Sarvam AI adapters (`SarvamResumeParser`, `SarvamDrafter`) — the same
`ResumeParser` / `Drafter` ports as the Gemini adapters, over Sarvam's
OpenAI-compatible chat-completions endpoint.

Endpoint, per docs.sarvam.ai (2026-09-13):
`POST https://api.sarvam.ai/v1/chat/completions`, auth header
`api-subscription-key: sk_...`, body `{model, messages, temperature,
response_format: {"type": "json_schema", "json_schema": {"name", "schema",
"strict": true}}}`, reply `choices[0].message.content` (a JSON string).

All HTTP is mocked with respx: no network, no key. The key must never
appear in any exception text or log record.
"""

import json

import httpx
import pytest
import respx

from app.adapters.sarvam import SarvamDrafter, SarvamResumeParser, SarvamError
from tests.adapters.test_gemini import (
    CLAIMS_JSON,
    CONTEXT,
    DRAFT_JSON,
    FIRST_EMAIL_JSON,
    FOLLOW_UP_CONTEXT,
)

API_KEY = "sk_super_secret"
MODEL = "sarvam-105b"
URL = "https://api.sarvam.ai/v1/chat/completions"


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _parser(**kw) -> SarvamResumeParser:
    kw.setdefault("_sleep", lambda s: None)
    return SarvamResumeParser(API_KEY, MODEL, **kw)


def _drafter(**kw) -> SarvamDrafter:
    kw.setdefault("_sleep", lambda s: None)
    return SarvamDrafter(API_KEY, MODEL, **kw)


# --- request shape -------------------------------------------------------------

@respx.mock
def test_parser_posts_to_the_chat_endpoint_with_the_subscription_key_header():
    route = respx.post(URL).mock(return_value=_ok(CLAIMS_JSON))
    claims = _parser().parse("resume text")
    assert len(claims) == 2
    req = route.calls[0].request
    assert req.headers["api-subscription-key"] == API_KEY
    body = json.loads(req.content)
    assert body["model"] == MODEL
    assert body["messages"][-1]["role"] == "user"
    assert "resume text" in body["messages"][-1]["content"]


@respx.mock
def test_parser_requests_strict_json_schema_output():
    route = respx.post(URL).mock(return_value=_ok(CLAIMS_JSON))
    _parser().parse("resume")
    body = json.loads(route.calls[0].request.content)
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    # JSON-schema (lowercase) types, not Gemini's upper-case OpenAPI enum
    assert schema["type"] == "object"
    item = schema["properties"]["claims"]["items"]
    assert item["properties"]["kind"]["enum"]
    assert body["temperature"] == 0


@respx.mock
def test_parser_unwraps_the_claims_envelope():
    # strict json_schema requires a top-level object, so the adapter asks
    # for {"claims": [...]} and returns the bare list the port promises.
    respx.post(URL).mock(return_value=_ok(json.dumps({"claims": json.loads(CLAIMS_JSON)})))
    claims = _parser().parse("resume")
    assert claims[0]["kind"] == "project"


@respx.mock
def test_drafter_sends_the_system_instruction_and_the_draft_schema():
    """A first email: the model is asked for the company paragraph only.

    This adapter is the one that actually runs (LLM_PROVIDER=sarvam), so
    the split has to be pinned HERE as well as in the Gemini tests - a
    change made only in gemini.py changes nothing the operator sees.
    """
    route = respx.post(URL).mock(return_value=_ok(FIRST_EMAIL_JSON))
    out = _drafter().draft(CONTEXT)
    assert set(out) >= {"subject", "company_paragraphs", "could_be_a_user",
                        "research_source_type", "research_source_id"}
    assert out["research_source_type"] == CONTEXT["available_sources"][0]["kind"]
    body = json.loads(route.calls[0].request.content)
    assert body["messages"][0]["role"] == "system"
    assert "one part of a cold email" in body["messages"][0]["content"].lower()
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["subject", "company_paragraphs", "could_be_a_user"]


@respx.mock
def test_a_follow_up_still_asks_this_adapter_for_a_whole_body():
    route = respx.post(URL).mock(return_value=_ok(DRAFT_JSON))
    out = _drafter().draft(FOLLOW_UP_CONTEXT)
    assert set(out) >= {"subject", "body", "research_source_type", "research_source_id"}
    schema = json.loads(route.calls[0].request.content)["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["subject", "body", "research_source_type", "research_source_id"]


@respx.mock
def test_drafter_uses_the_injected_model_id():
    route = respx.post(URL).mock(return_value=_ok(FIRST_EMAIL_JSON))
    SarvamDrafter(API_KEY, "some-other-model", _sleep=lambda s: None).draft(CONTEXT)
    assert json.loads(route.calls[0].request.content)["model"] == "some-other-model"


# --- failure handling ------------------------------------------------------------

@respx.mock
def test_drafter_raises_on_missing_fields():
    respx.post(URL).mock(return_value=_ok(json.dumps({"subject": "x"})))
    with pytest.raises(ValueError, match="missing"):
        _drafter().draft(CONTEXT)


@respx.mock
def test_non_json_content_raises_a_value_error():
    respx.post(URL).mock(return_value=_ok("Sure! Here is the JSON: {"))
    with pytest.raises(ValueError):
        _parser().parse("resume")


@respx.mock
def test_401_raises_sarvam_error_without_the_key():
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": {"message": f"bad key {API_KEY}"}}))
    with pytest.raises(SarvamError) as exc_info:
        _parser().parse("resume")
    assert exc_info.value.status == 401
    assert API_KEY not in str(exc_info.value)
    assert API_KEY not in repr(exc_info.value)


@respx.mock
def test_transport_error_raises_sarvam_error_without_the_key():
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(SarvamError) as exc_info:
        _drafter().draft(CONTEXT)
    assert API_KEY not in str(exc_info.value)


@respx.mock
def test_transient_429_and_503_are_retried_with_backoff():
    route = respx.post(URL)
    route.side_effect = [httpx.Response(503, text="busy"), httpx.Response(429, text="slow"),
                         _ok(FIRST_EMAIL_JSON)]
    sleeps: list[float] = []
    _drafter(_sleep=sleeps.append).draft(CONTEXT)
    assert route.call_count == 3
    assert sleeps == [5.0, 10.0]


@respx.mock
def test_retry_gives_up_after_three_attempts():
    route = respx.post(URL).mock(return_value=httpx.Response(503, text="busy"))
    with pytest.raises(SarvamError) as exc_info:
        _parser().parse("resume")
    assert route.call_count == 4
    assert exc_info.value.status == 503


@respx.mock
def test_402_insufficient_credits_is_not_retried():
    route = respx.post(URL).mock(return_value=httpx.Response(402, json={"error": {"message": "insufficient credits"}}))
    with pytest.raises(SarvamError) as exc_info:
        _parser().parse("resume")
    assert route.call_count == 1
    assert exc_info.value.status == 402


@respx.mock
def test_the_key_never_appears_in_logs(caplog):
    import logging
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": {"message": "nope"}}))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SarvamError):
            _parser().parse("resume")
    assert API_KEY not in caplog.text


def test_adapters_satisfy_the_ports():
    from app.ports import Drafter, ResumeParser
    assert isinstance(_parser(), ResumeParser)
    assert isinstance(_drafter(), Drafter)


# --- Live findings (2026-09-13) -------------------------------------------------
# `sarvam-105b` is a reasoning model: it spends the default 2048 max_tokens on
# `reasoning_content` and returns `content: null` with finish_reason "length".
# `sarvam-105b-conversations` does not reason (177 vs ~7000 completion tokens
# for the same parser prompt) and is the default; the adapter also sends an
# explicit max_tokens and turns a truncated/empty reply into a clear error.


def test_default_model_is_the_non_reasoning_conversations_variant():
    from app.adapters.sarvam import DEFAULT_MODEL
    assert DEFAULT_MODEL == "sarvam-105b-conversations"


@respx.mock
def test_request_sets_an_explicit_max_tokens_and_low_reasoning_effort():
    route = respx.post(URL).mock(return_value=_ok(CLAIMS_JSON))
    _parser().parse("resume")
    body = json.loads(route.calls[0].request.content)
    assert body["max_tokens"] >= 4096
    assert body["reasoning_effort"] == "low"


@respx.mock
def test_truncated_reply_with_null_content_raises_a_clear_sarvam_error():
    respx.post(URL).mock(return_value=httpx.Response(200, json={
        "choices": [{"index": 0, "finish_reason": "length",
                     "message": {"role": "assistant", "content": None, "reasoning_content": "thinking..."}}],
        "usage": {"completion_tokens": 2048},
    }))
    with pytest.raises(SarvamError) as exc_info:
        _parser().parse("resume")
    assert "truncated" in str(exc_info.value)
    assert API_KEY not in str(exc_info.value)
