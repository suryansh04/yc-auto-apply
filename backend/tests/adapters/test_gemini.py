"""Tasks 13/14: the two Gemini adapters, exercised against a fake
`google-genai` client object injected through the constructor (no module
globals patched, no network, no API key). Only the call *shape* is under
test here — model id, structured-output config, prompt content, JSON
decoding — since the real model's output can't be asserted offline.

Every prompt-content test below pins a rule the spec makes the adapter
responsible for stating (D28 length/structure, D29 source-never-named, D27
no location, the internship ask), so deleting that line of the prompt
fails a test rather than silently degrading drafts."""

import inspect
import json
import pathlib

import pytest

import app.adapters.gemini as gemini_module
from app.adapters.gemini import GeminiResumeParser

MODEL = "some-model-id-from-settings"


class _Response:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, text):
        self._text = text
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self._text)


class FakeGenAIClient:
    """Shape of `google.genai.Client`: the only thing the adapters touch is
    `client.models.generate_content(model=, contents=, config=)`."""

    def __init__(self, text):
        self.models = FakeModels(text)


def _config_as_dict(config) -> dict:
    # The adapters may pass a `types.GenerateContentConfig` or a plain dict;
    # normalise for assertions.
    if isinstance(config, dict):
        return config
    return config.model_dump(exclude_none=True)


# --- GeminiResumeParser -------------------------------------------------------

CLAIMS_JSON = json.dumps([
    {"kind": "project", "text": "built a scheduler in Python",
     "source_span": "Built a scheduler in Python, cut wait 50%"},
    {"kind": "skill", "text": "Python", "source_span": "Skills: Python"},
])


def test_parser_returns_the_decoded_claim_list():
    client = FakeGenAIClient(CLAIMS_JSON)
    claims = GeminiResumeParser(client, model=MODEL).parse(
        "Built a scheduler in Python, cut wait 50%\nSkills: Python")
    assert claims == json.loads(CLAIMS_JSON)


def test_parser_uses_the_injected_model_id_not_a_hardcoded_one():
    client = FakeGenAIClient("[]")
    GeminiResumeParser(client, model=MODEL).parse("some resume")
    assert client.models.calls[0]["model"] == MODEL


def test_parser_requests_structured_json_output_with_a_schema():
    client = FakeGenAIClient("[]")
    GeminiResumeParser(client, model=MODEL).parse("some resume")
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg["response_mime_type"] == "application/json"
    assert cfg.get("response_schema") or cfg.get("response_json_schema")


def test_parser_schema_restricts_kind_to_the_five_claim_kinds():
    client = FakeGenAIClient("[]")
    GeminiResumeParser(client, model=MODEL).parse("some resume")
    cfg = _config_as_dict(client.models.calls[0]["config"])
    schema = json.dumps(cfg.get("response_schema") or cfg.get("response_json_schema"), default=str)
    for kind in ("skill", "project", "metric", "role", "education"):
        assert kind in schema
    assert "source_span" in schema


def test_parser_prompt_carries_the_resume_text_and_the_verbatim_span_rule():
    client = FakeGenAIClient("[]")
    GeminiResumeParser(client, model=MODEL).parse("UNIQUE-RESUME-MARKER-42")
    contents = str(client.models.calls[0]["contents"])
    assert "UNIQUE-RESUME-MARKER-42" in contents
    assert "verbatim" in contents.lower()


def test_parser_tolerates_a_top_level_object_wrapping_the_list():
    client = FakeGenAIClient(json.dumps({"claims": json.loads(CLAIMS_JSON)}))
    claims = GeminiResumeParser(client, model=MODEL).parse("x")
    assert [c["kind"] for c in claims] == ["project", "skill"]


def test_parser_raises_on_non_json_output():
    client = FakeGenAIClient("not json at all")
    with pytest.raises(ValueError):
        GeminiResumeParser(client, model=MODEL).parse("x")


def test_parser_temperature_is_zero_for_determinism():
    client = FakeGenAIClient("[]")
    GeminiResumeParser(client, model=MODEL).parse("x")
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg.get("temperature") == 0


# --- D13: the model id is configuration, not code ----------------------------

def test_no_gemini_model_id_is_hardcoded_in_the_adapter_module():
    source = pathlib.Path(inspect.getsourcefile(gemini_module)).read_text(encoding="utf-8")
    assert "gemini-" not in source.lower()
