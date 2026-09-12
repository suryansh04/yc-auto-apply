"""Sarvam AI adapters: `SarvamResumeParser` (`ResumeParser` port) and
`SarvamDrafter` (`Drafter` port), over Sarvam's OpenAI-compatible
chat-completions endpoint.

Why a second LLM provider (2026-09-13, live run): Gemini's free tier allows
20 requests/day per model — unusable for ~279 founders x 2 calls — and the
operator chose Sarvam (Indian pricing, UPI top-up, ₹29/₹73 per 1M in/out
tokens on `sarvam-105b`, so a full run costs on the order of ₹50) over
enabling Google billing. The provider is selected by
`Settings.llm_provider`; the prompts, schemas and every rule the drafter is
told are shared with the Gemini adapter (`app.adapters.gemini`) so both
providers are held to exactly the same contract and the deterministic
validator downstream sees identical shapes.

Endpoint (docs.sarvam.ai, verified 2026-09-13):
`POST https://api.sarvam.ai/v1/chat/completions`, header
`api-subscription-key`, body `{model, messages, temperature,
response_format}` where `response_format = {"type": "json_schema",
"json_schema": {"name", "schema", "strict": true}}`; the assistant text is
`choices[0].message.content`. Strict JSON-schema mode requires a top-level
object, so the parser asks for `{"claims": [...]}` and unwraps it.

Secrets: the key travels only in a request header. `SarvamError` carries
the HTTP status and a short stage label — never the response body, the
URL, or the underlying httpx exception — so no traceback or log line can
leak it. httpx's own request log shows only the URL, which has no key.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import httpx

from app.adapters.gemini import (
    CLAIM_KINDS,
    SOURCE_KINDS,
    _DRAFTER_SYSTEM,
    _PARSER_PROMPT,
    _decode_json,
    build_draft_prompt,
)

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_URL = "https://api.sarvam.ai/v1/chat/completions"
DEFAULT_MODEL = "sarvam-105b"

_TRANSIENT_STATUS_CODES = {429, 503}
_MAX_TRANSIENT_RETRIES = 3
_BASE_BACKOFF_SECONDS = 5.0

# JSON-schema (lowercase) forms of the shared Gemini OpenAPI-style schemas.
# `additionalProperties: false` is what strict mode demands.
RESUME_CLAIMS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": CLAIM_KINDS},
                    "text": {"type": "string"},
                    "source_span": {"type": "string"},
                },
                "required": ["kind", "text", "source_span"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

DRAFT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "research_source_type": {"type": "string", "enum": SOURCE_KINDS},
        "research_source_id": {"type": "integer"},
    },
    "required": ["subject", "body", "research_source_type", "research_source_id"],
    "additionalProperties": False,
}


class SarvamError(RuntimeError):
    """A failed Sarvam call. Carries only the stage and HTTP status."""

    def __init__(self, stage: str, status: int | None = None, detail: str = ""):
        self.stage = stage
        self.status = status
        self.detail = detail
        bits = [f"sarvam {stage} failed"]
        if status is not None:
            bits.append(f"(HTTP {status})")
        if detail:
            bits.append(f"[{detail}]")
        super().__init__(" ".join(bits))


class _SarvamChat:
    """One chat-completions call with strict JSON-schema output, transient
    retry (429/503, 5s/10s/20s via the injectable `_sleep`) and key-safe
    errors. Shared by both adapters."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 120,
        _sleep: Callable[[float], None] = time.sleep,
        _client: httpx.Client | None = None,
    ):
        if not api_key:
            raise ValueError("SARVAM_API_KEY is not set")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._sleep = _sleep
        self._client = _client if _client is not None else httpx.Client()

    def close(self) -> None:
        self._client.close()

    def complete(
        self,
        stage: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        schema_name: str,
        *,
        temperature: float,
    ) -> str:
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
        }
        headers = {"api-subscription-key": self._api_key, "content-type": "application/json"}

        for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
            try:
                response = self._client.post(
                    CHAT_COMPLETIONS_URL, json=body, headers=headers, timeout=self._timeout
                )
            except httpx.HTTPError as exc:
                raise SarvamError(stage, detail=type(exc).__name__) from None

            status = response.status_code
            if status in _TRANSIENT_STATUS_CODES and attempt < _MAX_TRANSIENT_RETRIES:
                delay = _BASE_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning("%s: Sarvam %s, retrying in %.0fs", stage, status, delay)
                self._sleep(delay)
                continue
            break

        if status != 200:
            logger.info("%s: Sarvam error (HTTP %s)", stage, status)
            raise SarvamError(stage, status=status)

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise SarvamError(stage, status=status, detail="unexpected response shape") from None
        return content


class SarvamResumeParser:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, **chat_kwargs):
        self._chat = _SarvamChat(api_key, model, **chat_kwargs)

    def parse(self, raw_text: str) -> list[dict]:
        content = self._chat.complete(
            "resume parser",
            [{"role": "user", "content": _PARSER_PROMPT.format(resume=raw_text)}],
            RESUME_CLAIMS_JSON_SCHEMA,
            "resume_claims",
            temperature=0,
        )
        data = _decode_json(content, "resume parser")
        if isinstance(data, dict):
            data = data.get("claims") or next(
                (v for v in data.values() if isinstance(v, list)), []
            )
        if not isinstance(data, list):
            raise ValueError("resume parser: expected a JSON array of claims")
        return [c for c in data if isinstance(c, dict)]


class SarvamDrafter:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, **chat_kwargs):
        self._chat = _SarvamChat(api_key, model, **chat_kwargs)

    def draft(self, prompt_context: dict) -> dict:
        content = self._chat.complete(
            "drafter",
            [
                {"role": "system", "content": _DRAFTER_SYSTEM},
                {"role": "user", "content": build_draft_prompt(prompt_context)},
            ],
            DRAFT_JSON_SCHEMA,
            "outreach_draft",
            temperature=0.7,
        )
        data = _decode_json(content, "drafter")
        if not isinstance(data, dict):
            raise ValueError("drafter: expected a JSON object")
        missing = [k for k in ("subject", "body", "research_source_type", "research_source_id")
                   if k not in data]
        if missing:
            raise ValueError(f"drafter: response missing {', '.join(missing)}")
        return data
