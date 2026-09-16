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
from app.adapters.gemini import GeminiDrafter, GeminiResumeParser

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


# --- GeminiDrafter ------------------------------------------------------------

CONTEXT = {
    "company": {"name": "Vahan", "one_liner": "Recruitment over voice",
                "long_description": "Vahan matches blue-collar workers to jobs over voice in a dozen languages."},
    "founder": {"full_name": "Madhav Krishna", "title": "Founder", "bio": None},
    "available_sources": [
        {"kind": "news", "id": 7,
         "text": "OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI",
         "publication": "The Economic Times"},
        {"kind": "long_description", "id": 3,
         "text": "Vahan matches blue-collar workers to jobs over voice in a dozen languages."},
    ],
    "claims": [{"kind": "project", "text": "built a scheduler in Python"}],
    "sequence_step": 0,
}
DRAFT_JSON = json.dumps({
    "subject": "Backend intern at Vahan",
    "body": "You're matching blue-collar workers to jobs over voice at Vahan. Would you take an intern?",
    "research_source_type": "news",
    "research_source_id": 7,
})


def _prompt_of(client) -> str:
    call = client.models.calls[0]
    cfg = _config_as_dict(call["config"])
    return (str(call["contents"]) + " " + str(cfg.get("system_instruction", ""))).lower()


def test_drafter_returns_the_decoded_draft_dict():
    client = FakeGenAIClient(DRAFT_JSON)
    out = GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert out == json.loads(DRAFT_JSON)
    assert isinstance(out["research_source_id"], int)


def test_drafter_uses_the_injected_model_id():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert client.models.calls[0]["model"] == MODEL


def test_drafter_requests_structured_json_with_the_four_field_schema():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg["response_mime_type"] == "application/json"
    schema = json.dumps(cfg.get("response_schema") or cfg.get("response_json_schema"), default=str)
    for field in ("subject", "body", "research_source_type", "research_source_id"):
        assert field in schema
    for kind in ("news", "launch", "job_posting", "long_description"):
        assert kind in schema


def test_drafter_prompt_states_the_three_part_structure_and_length_targets():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert "three" in prompt and "part" in prompt
    assert "90" in prompt
    assert "120" in prompt
    assert "25 words" in prompt


def test_drafter_prompt_says_the_source_is_never_named_in_the_body():
    # D29: the source is how the sender learned something, never what the
    # sentence is about.
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert "never" in prompt
    assert "outlet" in prompt or "publication" in prompt
    assert "how you learned" in prompt or "how the sender learned" in prompt


def test_drafter_prompt_forbids_location_and_asks_for_a_swe_internship():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert "location" in prompt and "remote" in prompt
    assert "software engineering internship" in prompt


def test_drafter_prompt_restricts_claims_to_the_resume_and_forbids_greeting_and_signoff():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert "built a scheduler in python" in prompt          # the claim is offered
    assert "only" in prompt and "claims" in prompt           # ...and is the only allowed material
    assert "greeting" in prompt and "sign-off" in prompt


def test_drafter_prompt_lists_every_available_source_with_kind_and_id():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert "news" in prompt and "id=7" in prompt.replace(" ", "").replace(":", "=")
    assert "openai backs indian startup" in prompt
    assert "long_description" in prompt


def test_drafter_prompt_follow_up_word_target_matches_the_actual_step():
    # Fix round 1, mutation pin (survivor 2): a hardcoded
    # `target = FOLLOW_UP_WORD_TARGETS[1]` in `build_draft_prompt` passed
    # the whole suite because the only existing test built a step-1 prompt.
    from app.adapters.gemini import build_draft_prompt
    from app.domain.validation import FOLLOW_UP_WORD_TARGETS

    ctx = {**CONTEXT, "sequence_step": 2, "thread_history": [
        {"direction": "outbound", "sequence_step": 0, "subject": "s0", "body": "b0", "label": None},
        {"direction": "outbound", "sequence_step": 1, "subject": "s1", "body": "b1", "label": None},
    ]}
    prompt = build_draft_prompt(ctx)
    assert f"{FOLLOW_UP_WORD_TARGETS[2]} words" in prompt
    assert f"{FOLLOW_UP_WORD_TARGETS[1]} words" not in prompt


def test_system_prompt_bans_naming_the_earlier_email_as_a_channel():
    # Fix round 1, ruling T12-1: D29 ("the source is how you learned it,
    # never what the sentence is about") must also cover the sender's own
    # earlier email — "as I mentioned in my last email" is the same tell
    # pointed at oneself.
    low = gemini_module._DRAFTER_SYSTEM.lower()
    assert "my last email" in low or "my previous email" in low
    assert "as i mentioned" in low


def test_system_prompt_tells_the_model_an_automated_reply_is_not_the_founder():
    # Fix round 1, ruling T12-2: the only inbound row that can reach a
    # follow-up draft is machine-labelled (AUTO_REPLY/OUT_OF_OFFICE) — the
    # prompt must say so explicitly, not just "acknowledge their reply".
    low = gemini_module._DRAFTER_SYSTEM.lower()
    assert "automated reply" in low
    assert "not the founder" in low


def test_drafter_appends_validation_errors_from_a_retry_context():
    client = FakeGenAIClient(DRAFT_JSON)
    ctx = {**CONTEXT, "validation_errors": ["banned phrase: 'reach out'", "no explicit internship ask"],
           "previous_draft": {"subject": "old subject here now", "body": "old body"}}
    GeminiDrafter(client, model=MODEL).draft(ctx)
    prompt = _prompt_of(client)
    assert "banned phrase: 'reach out'" in prompt
    assert "no explicit internship ask" in prompt
    assert "old body" in prompt


def test_drafter_does_not_mention_validation_errors_on_a_first_attempt():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert "previous attempt" not in _prompt_of(client)


def test_drafter_coerces_a_string_source_id_to_int():
    client = FakeGenAIClient(json.dumps({**json.loads(DRAFT_JSON), "research_source_id": "7"}))
    out = GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert out["research_source_id"] == 7


def test_drafter_raises_on_a_response_missing_required_fields():
    client = FakeGenAIClient(json.dumps({"subject": "x"}))
    with pytest.raises(ValueError):
        GeminiDrafter(client, model=MODEL).draft(CONTEXT)


# --- fix round 1 ----------------------------------------------------------------

def test_f10_the_publication_name_is_not_handed_to_the_model():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert "economic times" not in _prompt_of(client)
    assert "publication" not in str(client.models.calls[0]["contents"]).lower()


def test_f11_the_prompt_requires_the_company_name_in_the_body():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert "name the company once in the body" in _prompt_of(client)


def test_f17_parser_drops_non_dict_items_from_the_array():
    client = FakeGenAIClient(json.dumps(["garbage", 42, None,
                                         {"kind": "skill", "text": "Python", "source_span": "Python"}]))
    claims = GeminiResumeParser(client, model=MODEL).parse("x")
    assert claims == [{"kind": "skill", "text": "Python", "source_span": "Python"}]


# --- D13: the model id is configuration, not code ----------------------------

def test_no_gemini_model_id_is_hardcoded_in_the_adapter_module():
    source = pathlib.Path(inspect.getsourcefile(gemini_module)).read_text(encoding="utf-8")
    assert "gemini-" not in source.lower()


# --- Live-run fix (2026-09-13): transient 503/429 from Gemini must be retried --
# Observed on the first real `parse-resume`: `google.genai.errors.ServerError`
# 503 UNAVAILABLE ("high demand") on two different flash models within
# minutes. Both adapters retry with backoff via an injectable `_sleep`.

from google.genai import errors as genai_errors

from app.adapters.gemini import GeminiDrafter, GeminiResumeParser


def _server_error(code):
    return genai_errors.APIError(code, {"error": {"code": code, "message": "high demand", "status": "UNAVAILABLE"}})


class FlakyModels(FakeModels):
    """Raises the queued errors first, then answers like FakeModels."""

    def __init__(self, text, failures):
        super().__init__(text)
        self._failures = list(failures)

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._failures:
            raise self._failures.pop(0)
        return _Response(self._text)


def _flaky_client(text, failures):
    c = FakeGenAIClient(text)
    c.models = FlakyModels(text, failures)
    return c


def test_parser_retries_a_503_with_backoff_then_succeeds():
    client = _flaky_client(CLAIMS_JSON, [_server_error(503), _server_error(503)])
    sleeps: list[float] = []
    claims = GeminiResumeParser(client, MODEL, _sleep=sleeps.append).parse("resume")
    assert len(claims) == 2
    assert len(client.models.calls) == 3
    assert sleeps == [5.0, 10.0]


def test_drafter_retries_a_429_then_succeeds():
    client = _flaky_client(DRAFT_JSON, [_server_error(429)])
    sleeps: list[float] = []
    out = GeminiDrafter(client, MODEL, _sleep=sleeps.append).draft(CONTEXT)
    assert out["subject"]
    assert len(client.models.calls) == 2
    assert sleeps == [5.0]


def test_parser_gives_up_after_three_retries_and_reraises():
    client = _flaky_client(CLAIMS_JSON, [_server_error(503)] * 4)
    with pytest.raises(genai_errors.APIError):
        GeminiResumeParser(client, MODEL, _sleep=lambda s: None).parse("resume")
    assert len(client.models.calls) == 4


def test_non_transient_errors_are_not_retried():
    client = _flaky_client(CLAIMS_JSON, [_server_error(404)])
    with pytest.raises(genai_errors.APIError):
        GeminiResumeParser(client, MODEL, _sleep=lambda s: None).parse("resume")
    assert len(client.models.calls) == 1
