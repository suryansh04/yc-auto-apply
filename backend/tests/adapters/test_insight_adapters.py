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
INSIGHT_JSON = json.dumps({
    "options": [{"style": "excited", "paragraph": PARAGRAPH}],
    "why": "the choice inside the product",
})


# --- GeminiInsightWriter -------------------------------------------------------


def test_gemini_writer_returns_the_paragraph_and_its_reason():
    client = FakeGenAIClient(INSIGHT_JSON)
    assert GeminiInsightWriter(client, model=MODEL).write(CONTEXT) == {
        "options": [{"style": "excited", "paragraph": PARAGRAPH}],
        "why": "the choice inside the product",
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
    item = config["response_schema"]["properties"]["options"]["items"]
    assert item["properties"]["tie_in"]["type"] == "STRING"
    assert item["properties"]["paragraph"]["type"] == "STRING"


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
    assert _writer().write(CONTEXT)["options"][0]["paragraph"] == PARAGRAPH
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
    assert rf["json_schema"]["schema"]["required"] == ["options", "why"]


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







def test_an_old_one_paragraph_answer_is_still_understood():
    from app.adapters.gemini import _insight

    assert _insight({"paragraph": "x", "why": "y"}, "t") == {
        "options": [{"style": "excited", "paragraph": "x"}], "why": "y",
    }


# --- his researched template (2026-09-25) -----------------------------------


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_both_prompts_carry_his_pipeline_rules():
    """His pipeline document (2026-09-25): the draft and the AI suggestion
    follow the same system prompt, so they share one set of rules."""
    from app.adapters.gemini import _EMAIL_RULES, _FIRST_EMAIL_SYSTEM, _INSIGHT_SYSTEM

    assert _EMAIL_RULES in _FIRST_EMAIL_SYSTEM
    assert _EMAIL_RULES in _INSIGHT_SYSTEM
    flat = _flat(_EMAIL_RULES)
    assert "If a fact is marked OLD, frame it as \"I read that...\"" in flat
    assert "caught my eye" in flat          # in the banned list
    assert "50 to 110 words" in flat


def test_his_three_examples_go_in_every_call():
    from app.adapters.gemini import EXAMPLE_EMAILS, build_draft_prompt, build_insight_prompt

    for name in ("WareIQ", "UrbanKisaan", "Kosh"):
        assert name in EXAMPLE_EMAILS
    ctx = {"company": {"name": "Zave"}, "founder": {"full_name": "Hiren Shah"},
           "available_sources": [], "sequence_step": 0}
    assert EXAMPLE_EMAILS in build_draft_prompt(ctx)
    assert EXAMPLE_EMAILS in build_insight_prompt({"company_name": "Zave", "description": "x"})


def test_the_writer_is_handed_only_the_verified_facts():
    from app.adapters.gemini import build_draft_prompt

    research = {"facts": [
        {"kind": "specific_thing", "text": "Aadi, a model pretrained on 49 plant genomes", "old": False},
        {"kind": "founder_fact", "text": "Spent time talking to farmers", "old": True},
    ], "question": "How do you clean phenotype data?", "skill_connection": ""}
    prompt = build_draft_prompt({"company": {"name": "UrbanKisaan"},
                                 "founder": {"full_name": "Vihari Kanukollu"},
                                 "available_sources": [], "sequence_step": 0,
                                 "research": research})
    assert "Founder first name: Vihari" in prompt
    assert "- Specific thing to mention: Aadi, a model pretrained on 49 plant genomes" in prompt
    assert "- Founder fact: Spent time talking to farmers (OLD)" in prompt
    assert "- Facts marked OLD: Spent time talking to farmers" in prompt


def test_the_link_to_him_is_only_when_real():
    from app.adapters.gemini import _EMAIL_RULES

    flat = _flat(_EMAIL_RULES)
    assert "If the connection is a stretch, skip it." in flat
    assert "is NOT backend work" in flat


def test_three_options_differ_in_what_they_notice():
    from app.adapters.gemini import INSIGHT_STYLES, _INSIGHT_SYSTEM

    assert INSIGHT_STYLES == ["template", "angle", "short"]
    assert "WRITE THREE OPTIONS" in _INSIGHT_SYSTEM


def test_an_option_is_the_tie_in_and_the_curious_paragraph_joined():
    from app.adapters.gemini import _insight

    out = _insight({"options": [{"style": "template", "tie_in": "A.", "paragraph": "B?"}],
                    "why": "w"}, "t")
    assert out["options"][0]["paragraph"] == "A.\n\nB?"
    assert out["options"][0]["tie_in"] == "A."
    assert out["options"][0]["company_paragraph"] == "B?"


def test_a_first_email_answer_becomes_two_company_paragraphs():
    from app.adapters.gemini import _first_email

    out = _first_email({"subject": "s", "tie_in": "Tie.", "paragraph": "Para?",
                        "founder_line": "Line.", "could_be_a_user": False},
                       {"available_sources": []}, "drafter")
    assert out["tie_in"] == "Tie."
    assert out["company_paragraphs"] == ["Para?"]
    assert out["founder_line"] == "Line."


# --- the founder line (2026-09-25) ---------------------------------------------


def test_the_founder_prompt_is_shared_with_sarvam_by_identity():
    from app.adapters import gemini, sarvam

    assert sarvam._FOUNDER_SYSTEM is gemini._FOUNDER_SYSTEM
    assert sarvam.build_founder_prompt is gemini.build_founder_prompt


def test_the_founder_prompt_carries_his_example_and_bans_where_it_was_found():
    from app.adapters.gemini import _FOUNDER_SYSTEM

    flat = _flat(_FOUNDER_SYSTEM)
    assert "spent time talking to drivers and factory workers" in flat
    assert "no LinkedIn" in flat


def test_the_founder_material_and_its_source_are_in_the_prompt():
    from app.adapters.gemini import build_founder_prompt

    prompt = build_founder_prompt({"founder_name": "Neel Balar", "company_name": "Clueso",
                                   "material": "Studied at IIT Madras.", "source": "a web search",
                                   "avoid": ["An earlier line."]})
    assert "FOUNDER MATERIAL (from a web search)" in prompt
    assert "IIT Madras" in prompt and "An earlier line." in prompt


@respx.mock
def test_sarvam_founder_writer_returns_the_options():
    from app.adapters.sarvam import SarvamFounderLineWriter

    answer = json.dumps({"options": [{"style": "short", "line": "A line."}], "why": "w"})
    route = respx.post(URL).mock(return_value=_ok(answer))
    out = SarvamFounderLineWriter(API_KEY, SARVAM_MODEL, _sleep=lambda s: None).write(
        {"founder_name": "Neel Balar", "material": "bio"})
    assert out == {"options": [{"style": "short", "line": "A line."}], "why": "w"}
    rf = json.loads(route.calls[0].request.content)["response_format"]
    assert rf["json_schema"]["schema"]["required"] == ["options", "why"]
