"""Task 9: `GeminiReplyClassifier` and `SarvamReplyClassifier` — the same
`ReplyClassifier` port over both providers, exercised the same way the
existing drafter/parser adapter tests are: a fake `google-genai` client
object for Gemini, `respx`-mocked HTTP for Sarvam. Only the call *shape* is
under test (model id, structured-output config, prompt content, JSON
decoding) — never a live provider call.
"""
import json

import httpx
import pytest
import respx

from app.adapters.gemini import CLASSIFY_SCHEMA, GeminiReplyClassifier, build_classify_prompt
from app.adapters.sarvam import REPLY_CLASSIFICATION_JSON_SCHEMA, SarvamReplyClassifier
from app.domain.replies import LABELS
from tests.adapters.test_gemini import FakeGenAIClient, _config_as_dict

MODEL = "some-model-id-from-settings"

CONTEXT = {
    "our_message": "You're building payments tooling for fleets. Would you take an intern?",
    "reply_body": "Sure, send me your resume.",
}
CLASSIFY_JSON = json.dumps({"label": "INTERESTED", "confidence": 0.85, "rationale": "asks for resume"})


# --- GeminiReplyClassifier -----------------------------------------------------


def test_gemini_classifier_returns_the_decoded_dict():
    client = FakeGenAIClient(CLASSIFY_JSON)
    out = GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)
    assert out == {"label": "INTERESTED", "confidence": 0.85, "rationale": "asks for resume"}


def test_gemini_classifier_uses_the_injected_model_id():
    client = FakeGenAIClient(CLASSIFY_JSON)
    GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)
    assert client.models.calls[0]["model"] == MODEL


def test_gemini_classifier_requests_structured_json_with_the_three_field_schema():
    client = FakeGenAIClient(CLASSIFY_JSON)
    GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg["response_mime_type"] == "application/json"
    schema = json.dumps(cfg.get("response_schema") or cfg.get("response_json_schema"), default=str)
    for field in ("label", "confidence", "rationale"):
        assert field in schema
    for label in LABELS:
        assert label in schema


def test_gemini_classifier_schema_matches_the_module_constant():
    assert CLASSIFY_SCHEMA["required"] == ["label", "confidence", "rationale"]
    assert set(CLASSIFY_SCHEMA["properties"]["label"]["enum"]) == set(LABELS)


def test_gemini_classifier_temperature_is_zero_for_determinism():
    client = FakeGenAIClient(CLASSIFY_JSON)
    GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg.get("temperature") == 0


def test_gemini_classifier_prompt_carries_both_our_message_and_the_reply():
    client = FakeGenAIClient(CLASSIFY_JSON)
    GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)
    contents = str(client.models.calls[0]["contents"])
    assert "payments tooling for fleets" in contents
    assert "send me your resume" in contents


def test_gemini_classifier_system_prompt_names_all_six_labels_and_the_autoresponder_rule():
    client = FakeGenAIClient(CLASSIFY_JSON)
    GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    system = str(cfg.get("system_instruction", ""))
    for label in LABELS:
        assert label in system
    assert "autoresponder" in system.lower()
    assert "25 words" in system


def test_gemini_classifier_raises_on_a_response_missing_required_fields():
    client = FakeGenAIClient(json.dumps({"label": "INTERESTED"}))
    with pytest.raises(ValueError):
        GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)


def test_gemini_classifier_raises_on_non_json_output():
    client = FakeGenAIClient("not json at all")
    with pytest.raises(ValueError):
        GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)


def test_gemini_classifier_coerces_a_string_confidence_to_float():
    client = FakeGenAIClient(json.dumps({**json.loads(CLASSIFY_JSON), "confidence": "0.85"}))
    out = GeminiReplyClassifier(client, model=MODEL).classify(CONTEXT)
    assert out["confidence"] == 0.85


def test_build_classify_prompt_is_shared_verbatim_with_sarvam():
    # The exact function both adapters call — proves neither one has
    # silently forked its own copy of the prompt-building logic.
    from app.adapters import sarvam

    assert sarvam.build_classify_prompt is build_classify_prompt


# --- SarvamReplyClassifier ------------------------------------------------------

API_KEY = "sk_super_secret"
SARVAM_MODEL = "sarvam-105b"
URL = "https://api.sarvam.ai/v1/chat/completions"


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _classifier(**kw) -> SarvamReplyClassifier:
    kw.setdefault("_sleep", lambda s: None)
    return SarvamReplyClassifier(API_KEY, SARVAM_MODEL, **kw)


@respx.mock
def test_sarvam_classifier_posts_to_the_chat_endpoint_with_the_subscription_key_header():
    route = respx.post(URL).mock(return_value=_ok(CLASSIFY_JSON))
    out = _classifier().classify(CONTEXT)
    assert out["label"] == "INTERESTED"
    req = route.calls[0].request
    assert req.headers["api-subscription-key"] == API_KEY
    body = json.loads(req.content)
    assert body["model"] == SARVAM_MODEL
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1]["role"] == "user"
    assert "send me your resume" in body["messages"][-1]["content"]


@respx.mock
def test_sarvam_classifier_requests_strict_json_schema_with_the_label_enum():
    route = respx.post(URL).mock(return_value=_ok(CLASSIFY_JSON))
    _classifier().classify(CONTEXT)
    body = json.loads(route.calls[0].request.content)
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]["label"]["enum"]) == set(LABELS)
    assert schema["required"] == ["label", "confidence", "rationale"]
    assert schema["additionalProperties"] is False
    assert body["temperature"] == 0


def test_sarvam_classifier_schema_matches_the_module_constant():
    assert REPLY_CLASSIFICATION_JSON_SCHEMA["required"] == ["label", "confidence", "rationale"]
    assert set(REPLY_CLASSIFICATION_JSON_SCHEMA["properties"]["label"]["enum"]) == set(LABELS)


@respx.mock
def test_sarvam_classifier_uses_the_injected_model_id():
    route = respx.post(URL).mock(return_value=_ok(CLASSIFY_JSON))
    SarvamReplyClassifier(API_KEY, "some-other-model", _sleep=lambda s: None).classify(CONTEXT)
    assert json.loads(route.calls[0].request.content)["model"] == "some-other-model"


@respx.mock
def test_sarvam_classifier_raises_on_missing_fields():
    respx.post(URL).mock(return_value=_ok(json.dumps({"label": "INTERESTED"})))
    with pytest.raises(ValueError, match="missing"):
        _classifier().classify(CONTEXT)


@respx.mock
def test_sarvam_classifier_non_json_content_raises_a_value_error():
    respx.post(URL).mock(return_value=_ok("Sure! Here is the JSON: {"))
    with pytest.raises(ValueError):
        _classifier().classify(CONTEXT)


@respx.mock
def test_sarvam_classifier_401_raises_sarvam_error_without_the_key():
    from app.adapters.sarvam import SarvamError

    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": {"message": f"bad key {API_KEY}"}}))
    with pytest.raises(SarvamError) as exc_info:
        _classifier().classify(CONTEXT)
    assert exc_info.value.status == 401
    assert API_KEY not in str(exc_info.value)


@respx.mock
def test_sarvam_classifier_transient_503_is_retried_with_backoff():
    route = respx.post(URL)
    route.side_effect = [httpx.Response(503, text="busy"), _ok(CLASSIFY_JSON)]
    sleeps: list[float] = []
    _classifier(_sleep=sleeps.append).classify(CONTEXT)
    assert route.call_count == 2
    assert sleeps == [5.0]


def test_sarvam_classifier_shares_the_system_prompt_with_gemini():
    from app.adapters.gemini import _CLASSIFIER_SYSTEM
    from app.adapters.sarvam import _CLASSIFIER_SYSTEM as sarvam_system

    assert sarvam_system is _CLASSIFIER_SYSTEM


# --- Port conformance -----------------------------------------------------------

def test_both_classifiers_satisfy_the_reply_classifier_port():
    from app.ports import ReplyClassifier

    gemini = GeminiReplyClassifier(FakeGenAIClient(CLASSIFY_JSON), model=MODEL)
    sarvam = _classifier()
    assert isinstance(gemini, ReplyClassifier)
    assert isinstance(sarvam, ReplyClassifier)


# --- deps.py wiring (build_classifier beside build_drafter) --------------------


def test_build_classifier_sarvam_requires_the_api_key():
    from app.api.deps import build_classifier
    from app.config import Settings

    settings = Settings(database_url="postgresql://x/y", llm_provider="sarvam", sarvam_api_key="")
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY"):
        build_classifier(settings)


def test_build_classifier_sarvam_builds_the_real_adapter():
    from app.api.deps import build_classifier
    from app.config import Settings

    settings = Settings(database_url="postgresql://x/y", llm_provider="sarvam", sarvam_api_key="sk_x", sarvam_model="sarvam-105b")
    classifier = build_classifier(settings)
    assert isinstance(classifier, SarvamReplyClassifier)


def test_build_classifier_gemini_requires_the_api_key():
    from app.api.deps import build_classifier
    from app.config import Settings

    settings = Settings(database_url="postgresql://x/y", llm_provider="gemini", gemini_api_key="")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_classifier(settings)
