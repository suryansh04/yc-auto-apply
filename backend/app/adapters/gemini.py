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
