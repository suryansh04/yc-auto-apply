"""Gemini adapters (D11, D13): `GeminiResumeParser` (Task 13) and
`GeminiDrafter` (Task 14), both over the `google-genai` SDK.

Design points shared by both classes:

- **The client is injected, never constructed here.** `__init__(client,
  model)` takes an already-built `google.genai.Client` (or anything with the
  same `client.models.generate_content(model=, contents=, config=)` shape)
  so tests exercise the exact call the SDK receives against a fake client
  object, with no module-global patching and no network. The CLI is the
  one place that builds the real client from `settings.gemini_api_key`.
- **The model id is configuration, not code (D13).** It arrives through the
  constructor from `settings.gemini_model`; nothing in this module names a
  model. `tests/adapters/test_gemini.py` greps this file for a hardcoded id.
- **Structured output, not free text (D11).** Every call sets
  `response_mime_type="application/json"` plus a `response_schema`, so the
  model's output is decoded with `json.loads` and shape-checked before it
  leaves the adapter — the validator (spec §10) then works on fields, not
  prose. `temperature=0` for the parser: claim extraction should be
  reproducible run to run.

SDK call shape (disclosed guess, verified only against the locally installed
`google-genai` package's signatures, not a live call):
`client.models.generate_content(model=str, contents=str,
config=types.GenerateContentConfig(...))`, response text on `.text`.
"""

from __future__ import annotations

import json
from typing import Any

from google.genai import types

CLAIM_KINDS = ["skill", "project", "metric", "role", "education"]

RESUME_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "kind": {"type": "STRING", "enum": CLAIM_KINDS},
            "text": {"type": "STRING"},
            "source_span": {"type": "STRING"},
        },
        "required": ["kind", "text", "source_span"],
    },
}

_PARSER_PROMPT = """You are extracting structured, citable claims from a student's resume.

Return a JSON array of claims. Each claim has:
- "kind": one of skill, project, metric, role, education
- "text": a short, plain restatement of one fact (one skill, one project,
  one measurable outcome, one role, one degree). No embellishment, no
  inferred seniority, nothing the resume does not literally say.
- "source_span": the exact, verbatim substring of the resume text below that
  the claim comes from. Copy it character for character - same case, same
  punctuation, same spacing. Do not paraphrase, trim words from the middle,
  or merge lines. A span that is not a verbatim substring is worthless.

Guidelines:
- One claim per fact. A line listing five technologies yields five "skill"
  claims, each with the same source_span.
- Prefer concrete, specific items (a named project and what it did, a
  number with its unit) over generic descriptors.
- Skip contact details, section headings, and filler.

RESUME TEXT:
<<<
{resume}
>>>
"""


def _decode_json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{what}: model returned non-JSON output") from exc


class GeminiResumeParser:
    """Real `ResumeParser` port (spec §8 stage 4). `parse(raw_text)` returns
    a list of `{kind, text, source_span}` dicts. Validation of those dicts
    (kind domain, verbatim span, empty text) is the use case's job
    (`app.application.parse_resume.ParseResume`), not the adapter's — the
    adapter only guarantees the *shape* the schema promised."""

    def __init__(self, client, model: str):
        self._client = client
        self._model = model

    def parse(self, raw_text: str) -> list[dict]:
        response = self._client.models.generate_content(
            model=self._model,
            contents=_PARSER_PROMPT.format(resume=raw_text),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESUME_CLAIM_SCHEMA,
                temperature=0,
            ),
        )
        data = _decode_json(response.text, "resume parser")
        # Structured output should give a bare array, but tolerate a model
        # that wraps it in a single-key object ({"claims": [...]}).
        if isinstance(data, dict):
            data = data.get("claims") or next(
                (v for v in data.values() if isinstance(v, list)), []
            )
        if not isinstance(data, list):
            raise ValueError("resume parser: expected a JSON array of claims")
        return [c for c in data if isinstance(c, dict)]


# --- Task 14: GeminiDrafter -----------------------------------------------------

SOURCE_KINDS = ["news", "launch", "job_posting", "long_description"]

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "subject": {"type": "STRING"},
        "body": {"type": "STRING"},
        "research_source_type": {"type": "STRING", "enum": SOURCE_KINDS},
        "research_source_id": {"type": "INTEGER"},
    },
    "required": ["subject", "body", "research_source_type", "research_source_id"],
}

_DRAFTER_SYSTEM = """You write short, plain cold emails from a student to a startup founder,
asking whether they would take on a software engineering intern. The email
must read like one specific person wrote it to one specific founder after
actually understanding what the company builds. It must never read like a
template, a press summary, or a CV.

STRUCTURE: exactly three parts, each its own paragraph, separated by a blank
line. Target about 90 words in total; the hard cap is 120. No sentence over
25 words. No paragraph over three sentences. No semicolons. At most two em
dashes. Not more than half the sentences may begin with "I".

Part 1 - the hook (one or two sentences): what they build AND why you are
writing, grounded in exactly one of the AVAILABLE SOURCES below. Name a
specific thing from that source (the product, the mechanism, the problem)
and give your own honest observation on it. Do not restate the company's
one-liner or tagline back at them.

Part 2 - why you are worth taking on (one or two sentences): cite at most
two of the RESUME CLAIMS below, ideally one concrete thing you built. Use
ONLY the resume claims listed. Never invent a skill, tool, metric, role or
institution that is not in the claims. Never imply professional seniority
(no "years of experience", "my team", "in my current role").

Part 3 - the ask (one plain sentence): ask whether they would take a
software engineering internship / intern. Speculative, not an application
to an advertised role. The words "intern" or "internship" must appear.

THE SOURCE IS HOW YOU LEARNED IT, NEVER WHAT THE SENTENCE IS ABOUT. The
research source (a news headline, a launch post, a posting, a description)
tells you a fact; it then disappears. Write about the thing the company
built, never about the coverage of it. Never name the outlet or publication,
never give a date, never write "I saw", "I read", "the article", "the
piece", "came across", "featured in" or anything else that refers to the
source as a source.

NEVER mention location, remote work, relocation, or where you are based.
Never use: "I hope this email finds you well", "I am reaching out", "reach
out", "passionate about", "leverage", "synergy", "game-changing", "I came
across your company", "delve", "in today's fast-paced", "circle back", "at
your earliest convenience", "I would love the opportunity", "please do not
hesitate".

OUTPUT: JSON with "subject" (three to six words, no emoji, no all-caps
words, naming the company), "body" (plain text, no markdown, no HTML, NO
greeting line and NO sign-off - those are added at send time), and
"research_source_type" / "research_source_id" naming exactly the one
source from AVAILABLE SOURCES that part 1 is grounded on.
"""


def _format_sources(sources: list[dict]) -> str:
    lines = []
    for s in sources:
        extra = f" (publication: {s['publication']})" if s.get("publication") else ""
        text = str(s.get("text", "")).replace("\n", " ").strip()
        lines.append(f"- kind={s.get('kind')} id={s.get('id')}{extra}: {text}")
    return "\n".join(lines) if lines else "- (none)"


def _format_claims(claims: list[dict]) -> str:
    lines = [f"- [{c.get('kind')}] {c.get('text')}" for c in claims]
    return "\n".join(lines) if lines else "- (none)"


def build_draft_prompt(context: dict) -> str:
    company = context.get("company") or {}
    founder = context.get("founder") or {}
    parts = [
        "COMPANY",
        f"name: {company.get('name')}",
        f"one_liner (do NOT restate this): {company.get('one_liner')}",
        "",
        "FOUNDER (the recipient)",
        f"name: {founder.get('full_name')}",
        f"title: {founder.get('title') or '(unknown)'}",
        f"bio: {founder.get('bio') or '(none)'}",
        "",
        "AVAILABLE SOURCES (ground part 1 on exactly one; cite its kind and id;",
        "listed in priority order - prefer the first one that gives you something specific)",
        _format_sources(context.get("available_sources") or []),
        "",
        "RESUME CLAIMS (the only things you may say about the sender)",
        _format_claims(context.get("claims") or []),
    ]
    errors = context.get("validation_errors")
    if errors:
        previous = context.get("previous_draft") or {}
        parts += [
            "",
            "YOUR PREVIOUS ATTEMPT WAS REJECTED. Fix every one of these problems:",
            "\n".join(f"- {e}" for e in errors),
            "",
            "Previous attempt (do not repeat its mistakes):",
            f"subject: {previous.get('subject')}",
            f"body: {previous.get('body')}",
        ]
    parts += ["", "Write the email now."]
    return "\n".join(parts)


class GeminiDrafter:
    """Real `Drafter` port (spec §10). `draft(prompt_context)` returns
    `{subject, body, research_source_type, research_source_id}`; the
    deterministic validator (`app.domain.validation.validate_draft`) is the
    use case's job, not this adapter's — the prompt states every rule so
    the model has a fair chance of passing, but nothing here trusts it."""

    def __init__(self, client, model: str):
        self._client = client
        self._model = model

    def draft(self, prompt_context: dict) -> dict:
        response = self._client.models.generate_content(
            model=self._model,
            contents=build_draft_prompt(prompt_context),
            config=types.GenerateContentConfig(
                system_instruction=_DRAFTER_SYSTEM,
                response_mime_type="application/json",
                response_schema=DRAFT_SCHEMA,
            ),
        )
        data = _decode_json(response.text, "drafter")
        if not isinstance(data, dict):
            raise ValueError("drafter: expected a JSON object")
        missing = [k for k in ("subject", "body", "research_source_type", "research_source_id")
                   if k not in data]
        if missing:
            raise ValueError(f"drafter: response missing {', '.join(missing)}")
        try:
            source_id = int(data["research_source_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("drafter: research_source_id is not an integer") from exc
        return {
            "subject": str(data["subject"]),
            "body": str(data["body"]),
            "research_source_type": str(data["research_source_type"]),
            "research_source_id": source_id,
        }
