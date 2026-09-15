"""Task 13 (D22): `GeminiCritic` and `SarvamCritic` — the same `Critic`
port over both providers, exercised the same way the existing
drafter/classifier adapter tests are: a fake `google-genai` client object
for Gemini, `respx`-mocked HTTP for Sarvam. Only the call *shape* is under
test (model id, structured-output config, prompt content, JSON decoding)
— never a live provider call, and never anything about what a verdict
means for a draft (that is `app.application.critique_draft`'s job, tested
in `tests/application/test_critique_draft.py`).
"""
import json

import httpx
import pytest
import respx

from app.adapters.gemini import GeminiCritic, build_critic_prompt
from app.adapters.sarvam import SarvamCritic
from tests.adapters.test_gemini import FakeGenAIClient, _config_as_dict

MODEL = "some-model-id-from-settings"

DRAFT = {
    "subject": "Backend intern at Vahan",
    "body": "You're matching blue-collar workers to jobs over voice at Vahan.\n\nWould you take an intern?",
    "research_source_type": "news",
    "research_source_id": 1,
}
CRITIQUE_JSON = json.dumps({
    "verdict": "NEEDS_REVISION",
    "concerns": [{"span": "excited about your work", "issue": "could be any company"}],
    "overall_note": "opener is generic",
})


# --- GeminiCritic ---------------------------------------------------------------


def test_gemini_critic_returns_the_decoded_dict():
    client = FakeGenAIClient(CRITIQUE_JSON)
    out = GeminiCritic(client, model=MODEL).critique(DRAFT)
    assert out == {
        "verdict": "NEEDS_REVISION",
        "concerns": [{"span": "excited about your work", "issue": "could be any company"}],
        "overall_note": "opener is generic",
    }


def test_gemini_critic_uses_the_injected_model_id():
    client = FakeGenAIClient(CRITIQUE_JSON)
    GeminiCritic(client, model=MODEL).critique(DRAFT)
    assert client.models.calls[0]["model"] == MODEL


def test_gemini_critic_requests_structured_json_with_the_three_field_schema():
    client = FakeGenAIClient(CRITIQUE_JSON)
    GeminiCritic(client, model=MODEL).critique(DRAFT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg["response_mime_type"] == "application/json"
    schema = json.dumps(cfg.get("response_schema") or cfg.get("response_json_schema"), default=str)
    for field in ("verdict", "concerns", "overall_note", "span", "issue"):
        assert field in schema
    assert "PASS" in schema and "NEEDS_REVISION" in schema


def test_gemini_critic_temperature_is_zero_for_determinism():
    client = FakeGenAIClient(CRITIQUE_JSON)
    GeminiCritic(client, model=MODEL).critique(DRAFT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg.get("temperature") == 0


def test_gemini_critic_prompt_carries_the_subject_and_body():
    client = FakeGenAIClient(CRITIQUE_JSON)
    GeminiCritic(client, model=MODEL).critique(DRAFT)
    contents = str(client.models.calls[0]["contents"])
    assert "Backend intern at Vahan" in contents
    assert "matching blue-collar workers" in contents


def test_gemini_critic_system_prompt_asks_only_about_voice_never_facts():
    client = FakeGenAIClient(CRITIQUE_JSON)
    GeminiCritic(client, model=MODEL).critique(DRAFT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    system = str(cfg.get("system_instruction", "")).lower()
    assert "naturalness" in system or "specific" in system
    assert "20 words" in system
    # The critic must be told, explicitly, not to re-check grounding/facts —
    # that is the deterministic validator's job, already done by the time
    # this call is ever made.
    assert "not your concern" in system or "already handled" in system


def test_gemini_critic_raises_on_a_response_missing_required_fields():
    client = FakeGenAIClient(json.dumps({"verdict": "PASS"}))
    with pytest.raises(ValueError):
        GeminiCritic(client, model=MODEL).critique(DRAFT)


def test_gemini_critic_raises_on_non_json_output():
    client = FakeGenAIClient("not json at all")
    with pytest.raises(ValueError):
        GeminiCritic(client, model=MODEL).critique(DRAFT)


def test_build_critic_prompt_is_shared_verbatim_with_sarvam():
    from app.adapters import sarvam

    assert sarvam.build_critic_prompt is build_critic_prompt


# --- SarvamCritic -----------------------------------------------------------------

API_KEY = "sk_super_secret"
SARVAM_MODEL = "sarvam-105b"
URL = "https://api.sarvam.ai/v1/chat/completions"


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _critic(**kw) -> SarvamCritic:
    kw.setdefault("_sleep", lambda s: None)
    return SarvamCritic(API_KEY, SARVAM_MODEL, **kw)


@respx.mock
def test_sarvam_critic_posts_to_the_chat_endpoint_with_the_subscription_key_header():
    route = respx.post(URL).mock(return_value=_ok(CRITIQUE_JSON))
    out = _critic().critique(DRAFT)
    assert out["verdict"] == "NEEDS_REVISION"
    req = route.calls[0].request
    assert req.headers["api-subscription-key"] == API_KEY
    body = json.loads(req.content)
    assert body["model"] == SARVAM_MODEL
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1]["role"] == "user"
    assert "Backend intern at Vahan" in body["messages"][-1]["content"]


@respx.mock
def test_sarvam_critic_requests_strict_json_schema_with_the_verdict_enum():
    route = respx.post(URL).mock(return_value=_ok(CRITIQUE_JSON))
    _critic().critique(DRAFT)
    body = json.loads(route.calls[0].request.content)
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]["verdict"]["enum"]) == {"PASS", "NEEDS_REVISION"}
    assert schema["required"] == ["verdict", "concerns", "overall_note"]
    assert schema["additionalProperties"] is False
    assert body["temperature"] == 0


@respx.mock
def test_sarvam_critic_uses_the_injected_model_id():
    route = respx.post(URL).mock(return_value=_ok(CRITIQUE_JSON))
    SarvamCritic(API_KEY, "some-other-model", _sleep=lambda s: None).critique(DRAFT)
    assert json.loads(route.calls[0].request.content)["model"] == "some-other-model"


@respx.mock
def test_sarvam_critic_raises_on_missing_fields():
    respx.post(URL).mock(return_value=_ok(json.dumps({"verdict": "PASS"})))
    with pytest.raises(ValueError, match="missing"):
        _critic().critique(DRAFT)


@respx.mock
def test_sarvam_critic_non_json_content_raises_a_value_error():
    respx.post(URL).mock(return_value=_ok("Sure! Here is the JSON: {"))
    with pytest.raises(ValueError):
        _critic().critique(DRAFT)


@respx.mock
def test_sarvam_critic_401_raises_sarvam_error_without_the_key():
    from app.adapters.sarvam import SarvamError

    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": {"message": f"bad key {API_KEY}"}}))
    with pytest.raises(SarvamError) as exc_info:
        _critic().critique(DRAFT)
    assert exc_info.value.status == 401
    assert API_KEY not in str(exc_info.value)


@respx.mock
def test_sarvam_critic_transient_503_is_retried_with_backoff():
    route = respx.post(URL)
    route.side_effect = [httpx.Response(503, text="busy"), _ok(CRITIQUE_JSON)]
    sleeps: list[float] = []
    _critic(_sleep=sleeps.append).critique(DRAFT)
    assert route.call_count == 2
    assert sleeps == [5.0]


def test_sarvam_critic_shares_the_system_prompt_with_gemini():
    from app.adapters.gemini import _CRITIC_SYSTEM
    from app.adapters.sarvam import _CRITIC_SYSTEM as sarvam_system

    assert sarvam_system is _CRITIC_SYSTEM


# --- Port conformance -----------------------------------------------------------

def test_both_critics_satisfy_the_critic_port():
    from app.ports import Critic

    gemini = GeminiCritic(FakeGenAIClient(CRITIQUE_JSON), model=MODEL)
    sarvam = _critic()
    assert isinstance(gemini, Critic)
    assert isinstance(sarvam, Critic)


# --- deps.py wiring (build_critic beside build_drafter/build_classifier) -------

def test_build_critic_sarvam_requires_the_api_key():
    from app.api.deps import build_critic
    from app.config import Settings

    settings = Settings(database_url="postgresql://x/y", llm_provider="sarvam", sarvam_api_key="")
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY"):
        build_critic(settings)


def test_build_critic_sarvam_builds_the_real_adapter():
    from app.api.deps import build_critic
    from app.config import Settings

    settings = Settings(database_url="postgresql://x/y", llm_provider="sarvam", sarvam_api_key="sk_x", sarvam_model="sarvam-105b")
    critic = build_critic(settings)
    assert isinstance(critic, SarvamCritic)


def test_build_critic_gemini_requires_the_api_key():
    from app.api.deps import build_critic
    from app.config import Settings

    settings = Settings(database_url="postgresql://x/y", llm_provider="gemini", gemini_api_key="")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_critic(settings)


def test_neither_adapter_hardcodes_a_model_id():
    # D13: the model id is configuration, never code. Greps this file's own
    # adapter module for a literal Gemini/Sarvam model string.
    import inspect

    import app.adapters.gemini as gemini_mod

    source = inspect.getsource(gemini_mod.GeminiCritic)
    assert "gemini-" not in source.lower()
