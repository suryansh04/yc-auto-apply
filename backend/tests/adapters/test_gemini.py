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

# 2026-09-22: a first email is ASSEMBLED from the operator's own blocks
# (app/domain/letter.py) and the model writes only the paragraph about the
# company, so step 0 has its own system prompt and its own schema. A
# follow-up still has the model write the whole body, which is what
# FOLLOW_UP_CONTEXT + DRAFT_JSON exercise below.
FIRST_EMAIL_JSON = json.dumps({
    "subject": "Backend intern at Vahan",
    "company_paragraphs": [
        "You didn't build another job board. You put the whole thing on a "
        "voice call at Vahan, which is the part I keep thinking about."
    ],
    # No source field at all: a first email is shown exactly ONE source
    # and never names it, so there is no token to paste into the letter.
    "could_be_a_user": False,
})
FOLLOW_UP_CONTEXT = {**CONTEXT, "sequence_step": 1, "thread_history": [
    {"direction": "outbound", "sequence_step": 0, "subject": "s0", "body": "b0", "label": None},
]}


def _prompt_of(client) -> str:
    call = client.models.calls[0]
    cfg = _config_as_dict(call["config"])
    return (str(call["contents"]) + " " + str(cfg.get("system_instruction", ""))).lower()


def test_drafter_returns_the_decoded_draft_dict():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    out = GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    # The source NUMBER is mapped back to our own (kind, id) here, so
    # nothing downstream has to know the model was shown a list.
    # 2026-09-25: a first email grounds on the founders' own description.
    assert (out["research_source_type"], out["research_source_id"]) == ("long_description", 3)
    assert out["company_paragraphs"] and out["could_be_a_user"] is False


def test_drafter_uses_the_injected_model_id():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert client.models.calls[0]["model"] == MODEL


def test_drafter_requests_structured_json_with_the_four_field_schema():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    assert cfg["response_mime_type"] == "application/json"
    schema = json.dumps(cfg.get("response_schema") or cfg.get("response_json_schema"), default=str)
    # A first email asks for the company paragraphs, not a whole body: the
    # rest of the letter is the operator's own and is assembled in code.
    # 2026-09-25: the two parts of his template - the tie-in and the
    # curious paragraph.
    for field in ("subject", "tie_in", "paragraph", "founder_line", "could_be_a_user"):
        assert field in schema
    # Deliberately absent: the source kinds. The model picks a NUMBER, so
    # our enum never reaches it - in the schema or the prompt - and it
    # cannot paste "long_description" into the letter.
    for kind in ("job_posting", "long_description"):
        assert kind not in schema


def test_a_follow_up_still_asks_for_a_whole_body():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(FOLLOW_UP_CONTEXT)
    cfg = _config_as_dict(client.models.calls[0]["config"])
    schema = json.dumps(cfg.get("response_schema") or cfg.get("response_json_schema"), default=str)
    assert "body" in schema
    assert "company_paragraphs" not in schema


def test_drafter_prompt_states_the_structure_and_a_length_range():
    # 2026-09-17: the caps were raised and the prompt now gives a RANGE and
    # a rhythm rule instead of hard per-sentence numbers - clipped uniform
    # sentences were themselves the machine tell.
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(FOLLOW_UP_CONTEXT)
    prompt = _prompt_of(client)
    assert "three" in prompt and "part" in prompt
    assert "180 to 240 words" in prompt
    assert "rhythm" in prompt.lower()
    assert "do not pad or clip" in prompt


def test_drafter_prompt_says_the_source_is_never_named_in_the_body():
    # D29: the source is how the sender learned something, never what the
    # sentence is about.
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert "never mention y combinator" in prompt
    assert "news outlet" in " ".join(prompt.split())


def test_drafter_prompt_forbids_location_and_asks_for_a_swe_internship():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(FOLLOW_UP_CONTEXT)
    prompt = _prompt_of(client)
    assert "location" in prompt and "remote" in prompt
    assert "sde / software engineering" in prompt


def test_drafter_prompt_restricts_claims_to_the_resume_and_forbids_greeting_and_signoff():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(FOLLOW_UP_CONTEXT)
    prompt = _prompt_of(client)
    assert "built a scheduler in python" in prompt          # the claim is offered
    assert "only" in prompt and "claims" in prompt           # ...and is the only allowed material
    assert "greeting" in prompt and "sign-off" in prompt


def test_drafter_prompt_lists_every_available_source_with_kind_and_id():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(FOLLOW_UP_CONTEXT)
    prompt = _prompt_of(client)
    # A FOLLOW-UP still gets the full numbered list; only a first email is
    # narrowed to one source.
    assert "source 1" in prompt and "source 2" in prompt
    import re as _re
    assert not _re.search(r"\[\s*\d+\s*\]", prompt)
    assert "openai backs indian startup" in prompt
    # Second person: the model pastes these labels into the letter.
    assert "a news story about you" in prompt
    assert "what you wrote about the company" in prompt
    assert "long_description" not in prompt


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


def test_system_prompt_asks_for_the_shared_problem_voice():
    # 2026-09-17: the operator supplied the template they want. The email
    # works by CONNECTION - the founder's own words, the same problem the
    # student has actually hit (from resume claims only), what they noticed
    # in the product, then the ask. Pins the guidance the model receives.
    low = gemini_module._DRAFTER_SYSTEM.lower()
    assert "connection" in low
    assert "same problem" in low
    assert "never invent" in low
    assert "the founder can check" in low
    assert "resume attached" in low
    assert "no praise" in low
    assert "summarises the company back" in low
    for tell in ("genuinely", "the exact", "resonates", "aligns", "impressive",
                 "fascinating", "incredible", "stacked adjectives", "contractions are fine"):
        assert tell in low, tell
    assert "tone only" in low
    # The hard constraints from before survive the rewrite.
    for kept in ("180 to 240 words", "five paragraphs",
                 "never mention location",
                 "years of experience", "the source is how you learned it",
                 "\"intern\" or \"internship\" must", "follow-ups:"):
        assert kept in low, kept


def test_drafter_appends_validation_errors_from_a_retry_context():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    ctx = {**CONTEXT, "validation_errors": ["banned phrase: 'reach out'", "no explicit internship ask"],
           "previous_draft": {"subject": "old subject here now", "body": "old body"}}
    GeminiDrafter(client, model=MODEL).draft(ctx)
    prompt = _prompt_of(client)
    assert "banned phrase: 'reach out'" in prompt
    assert "no explicit internship ask" in prompt
    assert "old body" in prompt


def test_drafter_does_not_mention_validation_errors_on_a_first_attempt():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert "previous attempt" not in _prompt_of(client)


def test_a_first_email_is_grounded_on_the_top_priority_source():
    """The model is shown one source and never names it, so the citation
    is known without asking - and cannot be pasted into the letter."""
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    out = GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    # 2026-09-25: the founders' own description comes first for a first
    # email - his template needs a concrete thing the product does, and a
    # news headline rarely has one.
    assert (out["research_source_type"], out["research_source_id"]) == ("long_description", 3)


def test_a_first_email_is_shown_only_the_top_source():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert "in a dozen languages" in prompt
    # The other source is not offered at all, so there is no list, no
    # numbering, and nothing to cite.
    assert "openai backs indian startup" not in prompt
    assert "source 1" not in prompt


def test_drafter_raises_on_a_response_missing_required_fields():
    client = FakeGenAIClient(json.dumps({"subject": "x"}))
    with pytest.raises(ValueError):
        GeminiDrafter(client, model=MODEL).draft(CONTEXT)


# --- fix round 1 ----------------------------------------------------------------

def test_f10_the_publication_name_is_not_handed_to_the_model():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert "economic times" not in _prompt_of(client)
    assert "publication" not in str(client.models.calls[0]["contents"]).lower()


def test_f11_the_prompt_requires_the_company_name_in_the_body():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(FOLLOW_UP_CONTEXT)
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
    client = _flaky_client(FIRST_EMAIL_JSON, [_server_error(429)])
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


def test_first_email_prompt_ends_with_the_one_paragraph_instruction():
    # Live finding (2026-09-17): structure that lives only in the long
    # system prompt gets skipped by a small model. The instruction goes at
    # the END of the user prompt, where it still attends. Since 2026-09-22
    # that instruction is "write ONLY the company paragraph" - the rest of
    # the letter is assembled from the operator's own blocks.
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    prompt = _prompt_of(client)
    assert 'write only "tie_in"' in prompt
    assert "not yours to produce" in prompt
    assert prompt.index("write only") < prompt.index("write the email now")

def test_a_follow_up_prompt_has_no_first_email_checklist():
    # A follow-up is one short paragraph - the four-part checklist would be
    # actively wrong there.
    client = FakeGenAIClient(DRAFT_JSON)
    ctx = {**CONTEXT, "sequence_step": 1, "thread_history": []}
    GeminiDrafter(client, model=MODEL).draft(ctx)
    assert "five entries, in this order" not in _prompt_of(client)


def test_claims_are_grouped_by_the_resume_line_they_came_from():
    # Live finding (2026-09-17): a flat claim list let the model write "I
    # ran into that on WaveWatch, where I had to wire four monitoring tools
    # together" - the wiring was a different job. Grouping shows which
    # facts belong to the same thing.
    out = gemini_module._format_claims([
        {"kind": "project", "text": "WaveWatch is a coastal system.", "source_span": "A"},
        {"kind": "metric", "text": "cut grading 70%", "source_span": "A"},
        {"kind": "role", "text": "Intern at Better", "source_span": "B"},
    ])
    assert "FACT GROUP 1" in out and "FACT GROUP 2" in out
    assert out.index("WaveWatch") < out.index("FACT GROUP 2")
    assert out.index("Intern at Better") > out.index("FACT GROUP 2")


def test_the_prompt_forbids_mixing_two_fact_groups():
    client = FakeGenAIClient(DRAFT_JSON)
    GeminiDrafter(client, model=MODEL).draft(FOLLOW_UP_CONTEXT)
    assert "never attribute a detail from one group" in _prompt_of(client)


def test_the_prompt_forbids_claiming_to_have_used_the_product():
    client = FakeGenAIClient(FIRST_EMAIL_JSON)
    GeminiDrafter(client, model=MODEL).draft(CONTEXT)
    assert "never claim he used, signed up for or tested" in " ".join(_prompt_of(client).split())


def test_the_tone_example_is_marked_as_not_the_company_being_written_to():
    # The example's words ("planner", "planning layer") were appearing in
    # emails to companies with no planner at all.
    low = gemini_module._DRAFTER_SYSTEM.lower()
    assert "never\nreuse its words" in low or "never reuse its words" in low
    assert "you have copied the example" in low
