"""`GeminiInsightWriter` and `SarvamInsightWriter` - the same
`InsightWriter` port over both providers, exercised the way the reviser
adapters are: a fake `google-genai` client for Gemini, `respx`-mocked HTTP
for Sarvam. Only the call shape is under test (model id, structured
output, prompt content, JSON decoding) - never a live provider call, and
never whether an answer is worth showing (that is
`app.application.write_insight`).

The parity tests at the bottom matter most, for the reason the reviser's
do: a change made in `gemini.py` alone, while `LLM_PROVIDER=sarvam`,
changes nothing the operator sees.
"""
import json

import httpx
import pytest
import respx

from app.adapters.gemini import GeminiInsightWriter, build_insight_prompt
from app.adapters.sarvam import SarvamInsightWriter
from tests.adapters.test_gemini import FakeGenAIClient, _config_as_dict

MODEL = "some-model-id-from-settings"

CONTEXT = {
    "company_name": "Loop Health",
    "one_liner": "Modern & comprehensive health insurance plan for employees in India",
    "description": (
        "Loop Health is a new type of healthcare provider in India combining high-quality "
        "primary care and insurance for employers. Employees get unlimited primary care "
        "visits at our asset-light clinics, alongside traditional hospitalization insurance."
    ),
}
PARAGRAPH = (
    "I like how Loop puts doctors and insurers on the same side. Most health insurance in "
    "India only kicks in when you land in a hospital, so people pay for it every year and "
    "barely use it. You turned it into something people actually use day to day."
)
INSIGHT_JSON = json.dumps({"paragraph": PARAGRAPH, "why": "the choice inside the product"})


# --- GeminiInsightWriter -------------------------------------------------------


def test_gemini_writer_returns_the_paragraph_and_its_reason():
    client = FakeGenAIClient(INSIGHT_JSON)
    assert GeminiInsightWriter(client, model=MODEL).write(CONTEXT) == {
        "paragraph": PARAGRAPH, "why": "the choice inside the product",
    }


def test_gemini_writer_uses_the_injected_model_id():
    client = FakeGenAIClient(INSIGHT_JSON)
    GeminiInsightWriter(client, model=MODEL).write(CONTEXT)
    assert client.models.calls[0]["model"] == MODEL


def test_gemini_writer_requests_structured_json():
    client = FakeGenAIClient(INSIGHT_JSON)
    GeminiInsightWriter(client, model=MODEL).write(CONTEXT)
    config = _config_as_dict(client.models.calls[0]["config"])
    assert config["response_mime_type"] == "application/json"
    assert config["response_schema"]["properties"]["paragraph"]["type"] == "STRING"


def test_gemini_writer_raises_on_non_json_output():
    with pytest.raises(ValueError):
        GeminiInsightWriter(FakeGenAIClient("not json at all"), model=MODEL).write(CONTEXT)


def test_the_company_description_is_what_the_model_is_given():
    """The operator's actual request: the source is the text the founders
    wrote for their own YC page."""
    prompt = build_insight_prompt(CONTEXT)
    assert "Loop Health" in prompt
    assert "unlimited primary care" in prompt
    assert "Modern & comprehensive health insurance" in prompt


def test_the_prompt_forbids_reciting_their_metrics():
    """Every number in a YC description is a fundraising fact, and
    repeating one back reads as reciting their own page at them."""
    from app.adapters.gemini import _INSIGHT_SYSTEM

    assert "NO NUMBERS" in _INSIGHT_SYSTEM
    assert "Y Combinator" in _INSIGHT_SYSTEM  # ...and never to mention where it came from


def test_the_prompt_describes_the_standard_without_quoting_it():
    """Live finding (2026-09-23): the operator's own Loop Health sentence
    was in the prompt as the worked example, and Sarvam wrote it again
    for a bus company and a robotics company - "it is showing the same
    for orangewood and zingbus". An example is a phrase a model reaches
    for, so the one phrase that must not repeat cannot be in there."""
    from app.adapters.gemini import _INSIGHT_SYSTEM

    assert "doctors and insurers" not in _INSIGHT_SYSTEM
    # Described instead, and banned by name.
    assert "health insurance company" in _INSIGHT_SYSTEM
    assert '"on the same side"' in _INSIGHT_SYSTEM


def test_what_has_already_been_offered_is_named_in_the_prompt():
    """"Write me another" and "write me one" are the same request unless
    the model is told what it already produced."""
    prompt = build_insight_prompt({**CONTEXT, "avoid": ["An earlier attempt."]})
    assert "ALREADY WRITTEN FOR THIS COMPANY" in prompt
    assert "An earlier attempt." in prompt
    assert "ALREADY WRITTEN" not in build_insight_prompt(CONTEXT)


# --- SarvamInsightWriter -------------------------------------------------------

API_KEY = "sk_super_secret"
SARVAM_MODEL = "sarvam-105b"
URL = "https://api.sarvam.ai/v1/chat/completions"


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _writer(**kw) -> SarvamInsightWriter:
    kw.setdefault("_sleep", lambda s: None)
    return SarvamInsightWriter(API_KEY, SARVAM_MODEL, **kw)


@respx.mock
def test_sarvam_writer_posts_the_description_with_the_subscription_key_header():
    route = respx.post(URL).mock(return_value=_ok(INSIGHT_JSON))
    assert _writer().write(CONTEXT)["paragraph"] == PARAGRAPH
    req = route.calls[0].request
    assert req.headers["api-subscription-key"] == API_KEY
    body = json.loads(req.content)
    assert body["model"] == SARVAM_MODEL
    assert "unlimited primary care" in body["messages"][-1]["content"]


@respx.mock
def test_sarvam_writer_requests_strict_json_schema():
    route = respx.post(URL).mock(return_value=_ok(INSIGHT_JSON))
    _writer().write(CONTEXT)
    rf = json.loads(route.calls[0].request.content)["response_format"]
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["required"] == ["paragraph", "why"]


@respx.mock
def test_sarvam_writer_401_raises_without_the_key():
    from app.adapters.sarvam import SarvamError

    respx.post(URL).mock(return_value=httpx.Response(
        401, json={"error": {"message": f"bad key {API_KEY}"}}))
    with pytest.raises(SarvamError) as exc_info:
        _writer().write(CONTEXT)
    assert API_KEY not in str(exc_info.value)


# --- parity ---------------------------------------------------------------------


def test_the_prompt_is_shared_with_sarvam_by_identity():
    from app.adapters import sarvam

    assert sarvam.build_insight_prompt is build_insight_prompt


def test_the_system_prompt_is_shared_with_sarvam_by_identity():
    from app.adapters import gemini, sarvam

    assert sarvam._INSIGHT_SYSTEM is gemini._INSIGHT_SYSTEM


@respx.mock
def test_both_providers_are_sent_the_same_user_prompt():
    respx.post(URL).mock(return_value=_ok(INSIGHT_JSON))
    _writer().write(CONTEXT)
    sarvam_prompt = json.loads(respx.calls[0].request.content)["messages"][-1]["content"]

    client = FakeGenAIClient(INSIGHT_JSON)
    GeminiInsightWriter(client, model=MODEL).write(CONTEXT)
    assert str(client.models.calls[0]["contents"]) == sarvam_prompt


# --- the subject writer shares its prompt the same way (2026-09-23) ---------


def test_the_subject_prompt_is_shared_with_sarvam_by_identity():
    from app.adapters import gemini, sarvam

    assert sarvam._SUBJECT_SYSTEM is gemini._SUBJECT_SYSTEM
    assert sarvam.build_subject_prompt is gemini.build_subject_prompt


@respx.mock
def test_sarvam_subject_writer_returns_the_list():
    from app.adapters.sarvam import SarvamSubjectWriter

    route = respx.post(URL).mock(return_value=_ok(json.dumps({"subjects": ["a b c", "d e f"]})))
    out = SarvamSubjectWriter(API_KEY, SARVAM_MODEL, _sleep=lambda s: None).write(
        {"company_name": "Loop Health", "description": "Clinics and cover."})
    assert out == ["a b c", "d e f"]
    rf = json.loads(route.calls[0].request.content)["response_format"]
    assert rf["json_schema"]["schema"]["required"] == ["subjects"]


def test_the_subject_prompt_bans_fake_replies_and_the_generic_ones():
    from app.adapters.gemini import _SUBJECT_SYSTEM

    for banned in ('"Re:"', '"Internship inquiry"', '"Quick question"'):
        assert banned in _SUBJECT_SYSTEM


def test_the_paragraph_ends_on_why_he_is_writing():
    """Operator request (2026-09-23): "show excitement about them and also
    why i am applying". The closing line is required, not optional, and it
    must not use the phrases the rest of the letter's checks reject."""
    from app.adapters.gemini import _INSIGHT_SYSTEM

    assert "ALWAYS end on HIM" in _INSIGHT_SYSTEM
    assert "main reason I'm writing to you" in _INSIGHT_SYSTEM
    assert 'Never "reaching out"' in _INSIGHT_SYSTEM
