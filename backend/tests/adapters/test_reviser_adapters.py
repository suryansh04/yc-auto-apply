"""`GeminiReviser` and `SarvamReviser` - the same `Reviser` port over both
providers, exercised the way the critic adapters are: a fake
`google-genai` client for Gemini, `respx`-mocked HTTP for Sarvam. Only the
call shape is under test (model id, structured output, prompt content,
JSON decoding) - never a live provider call, and never anything about
which rewrite is worth showing (that is
`app.application.suggest_fixes`).

The parity tests at the bottom are the ones that matter most. A live
finding from 2026-09-20 stands behind them: a change made in `gemini.py`
alone, while `LLM_PROVIDER=sarvam`, passed every new test and changed
nothing the operator saw. The prompt is shared by identity here so that
cannot happen again.
"""
import json

import httpx
import pytest
import respx

from app.adapters.gemini import GeminiReviser, build_revise_prompt
from app.adapters.sarvam import SarvamReviser
from tests.adapters.test_gemini import FakeGenAIClient, _config_as_dict

MODEL = "some-model-id-from-settings"

CONTEXT = {
    "company_name": "Loop Health",
    "body": "You built a dedicated plan for India's workforce.\n\nWould you take an intern?",
    "spans": [
        {"index": 2, "quote": "You built a dedicated plan for India's workforce.",
         "note": "hook overlaps more than 60% with the one-liner"},
    ],
}
REVISIONS_JSON = json.dumps({"revisions": [
    {"index": 2, "replacement": "Your plan is built for India's workforce, not ported to it.",
     "why": "says something the one-liner does not"},
]})


# --- GeminiReviser --------------------------------------------------------------


def test_gemini_reviser_returns_one_entry_per_rewrite():
    client = FakeGenAIClient(REVISIONS_JSON)
    out = GeminiReviser(client, model=MODEL).revise(CONTEXT)
    assert out == [{
        "index": 2,
        "replacement": "Your plan is built for India's workforce, not ported to it.",
        "why": "says something the one-liner does not",
    }]


def test_gemini_reviser_uses_the_injected_model_id():
    client = FakeGenAIClient(REVISIONS_JSON)
    GeminiReviser(client, model=MODEL).revise(CONTEXT)
    assert client.models.calls[0]["model"] == MODEL


def test_gemini_reviser_requests_structured_json_with_the_three_field_schema():
    client = FakeGenAIClient(REVISIONS_JSON)
    GeminiReviser(client, model=MODEL).revise(CONTEXT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg["response_mime_type"] == "application/json"
    schema = json.dumps(cfg.get("response_schema") or cfg.get("response_json_schema"), default=str)
    for field in ("revisions", "index", "replacement", "why"):
        assert field in schema


def test_gemini_reviser_prompt_carries_the_letter_and_every_flagged_span():
    client = FakeGenAIClient(REVISIONS_JSON)
    GeminiReviser(client, model=MODEL).revise(CONTEXT)
    contents = str(client.models.calls[0]["contents"])
    assert "Would you take an intern?" in contents          # the letter, for context
    assert "[2]" in contents                                 # the span's index, to answer with
    assert "hook overlaps more than 60%" in contents         # what the checker objected to


def test_gemini_reviser_system_prompt_forbids_inventing_facts_and_allows_skipping():
    client = FakeGenAIClient(REVISIONS_JSON)
    GeminiReviser(client, model=MODEL).revise(CONTEXT)
    system = str(_config_as_dict(client.models.calls[0]["config"]).get("system_instruction", "")).lower()
    # The fabrication class this whole letter pipeline was rebuilt to
    # kill: a rewrite must not be allowed to introduce a claim.
    assert "never invent a fact" in system
    # Skipping has to be explicitly legal, or the model invents a rewrite
    # for a span it has nothing better for.
    assert "skip" in system
    assert "span alone" in system


def test_gemini_reviser_survives_junk_instead_of_raising():
    # An unusable answer means no suggestions, not a failed request: the
    # checks are already on the operator's screen and must stay there.
    client = FakeGenAIClient(json.dumps({"revisions": [{"replacement": "no index"}, "nonsense"]}))
    assert GeminiReviser(client, model=MODEL).revise(CONTEXT) == []


def test_gemini_reviser_raises_on_non_json_output():
    client = FakeGenAIClient("not json at all")
    with pytest.raises(ValueError):
        GeminiReviser(client, model=MODEL).revise(CONTEXT)


# --- SarvamReviser ----------------------------------------------------------------

API_KEY = "sk_super_secret"
SARVAM_MODEL = "sarvam-105b"
URL = "https://api.sarvam.ai/v1/chat/completions"


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _reviser(**kw) -> SarvamReviser:
    kw.setdefault("_sleep", lambda s: None)
    return SarvamReviser(API_KEY, SARVAM_MODEL, **kw)


@respx.mock
def test_sarvam_reviser_posts_to_the_chat_endpoint_with_the_subscription_key_header():
    route = respx.post(URL).mock(return_value=_ok(REVISIONS_JSON))
    out = _reviser().revise(CONTEXT)
    assert out[0]["index"] == 2
    req = route.calls[0].request
    assert req.headers["api-subscription-key"] == API_KEY
    body = json.loads(req.content)
    assert body["model"] == SARVAM_MODEL
    assert body["messages"][0]["role"] == "system"
    assert "hook overlaps more than 60%" in body["messages"][-1]["content"]


@respx.mock
def test_sarvam_reviser_requests_strict_json_schema():
    route = respx.post(URL).mock(return_value=_ok(REVISIONS_JSON))
    _reviser().revise(CONTEXT)
    rf = json.loads(route.calls[0].request.content)["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    assert schema["required"] == ["revisions"]
    assert schema["additionalProperties"] is False
    item = schema["properties"]["revisions"]["items"]
    assert item["required"] == ["index", "replacement", "why"]


@respx.mock
def test_sarvam_reviser_401_raises_sarvam_error_without_the_key():
    from app.adapters.sarvam import SarvamError

    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": {"message": f"bad key {API_KEY}"}}))
    with pytest.raises(SarvamError) as exc_info:
        _reviser().revise(CONTEXT)
    assert exc_info.value.status == 401
    assert API_KEY not in str(exc_info.value)


@respx.mock
def test_sarvam_reviser_transient_503_is_retried_with_backoff():
    route = respx.post(URL)
    route.side_effect = [httpx.Response(503, text="busy"), _ok(REVISIONS_JSON)]
    sleeps: list[float] = []
    _reviser(_sleep=sleeps.append).revise(CONTEXT)
    assert route.call_count == 2
    assert sleeps == [5.0]


# --- parity ------------------------------------------------------------------------


def test_the_prompt_is_shared_with_sarvam_by_identity():
    from app.adapters import sarvam

    assert sarvam.build_revise_prompt is build_revise_prompt


def test_the_system_prompt_is_shared_with_sarvam_by_identity():
    from app.adapters import gemini, sarvam

    assert sarvam._REVISER_SYSTEM is gemini._REVISER_SYSTEM


@respx.mock
def test_both_providers_are_sent_the_same_user_prompt():
    respx.post(URL).mock(return_value=_ok(REVISIONS_JSON))
    _reviser().revise(CONTEXT)
    sarvam_prompt = json.loads(respx.calls[0].request.content)["messages"][-1]["content"]

    client = FakeGenAIClient(REVISIONS_JSON)
    GeminiReviser(client, model=MODEL).revise(CONTEXT)
    assert str(client.models.calls[0]["contents"]) == sarvam_prompt
