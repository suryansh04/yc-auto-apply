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
    _CLASSIFIER_SYSTEM,
    _CRITIC_SYSTEM,
    _DRAFTER_SYSTEM,
    _FIRST_EMAIL_SYSTEM,
    _INSIGHT_SYSTEM,
    _PARSER_PROMPT,
    _REVISER_SYSTEM,
    _decode_json,
    _insight,
    _revisions,
    build_classify_prompt,
    build_critic_prompt,
    build_draft_prompt,
    build_insight_prompt,
    build_revise_prompt,
    first_email_source,
)
from app.domain.replies import LABELS

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_URL = "https://api.sarvam.ai/v1/chat/completions"
# Live finding (2026-09-13): `sarvam-105b` is a reasoning model and spends
# thousands of output tokens (and the default 2048 max_tokens) on
# `reasoning_content` before answering; the `-conversations` variant does not
# reason, answered the same parser prompt in 177 tokens, and is ~40x cheaper.
DEFAULT_MODEL = "sarvam-105b-conversations"
_MAX_TOKENS = 4096

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
        # Mirror of gemini.DRAFT_SCHEMA: one array entry per paragraph, so
        # the four-part structure survives a model that ignores prose.
        "body": {"type": "array", "items": {"type": "string"}},
        "research_source_type": {"type": "string", "enum": SOURCE_KINDS},
        "research_source_id": {"type": "integer"},
    },
    "required": ["subject", "body", "research_source_type", "research_source_id"],
    "additionalProperties": False,
}

# Mirror of gemini.FIRST_EMAIL_SCHEMA. A first email is assembled from the
# operator's own blocks (app/domain/letter.py) and the model writes only
# the paragraph about the company.
#
# This adapter is the one that actually runs (LLM_PROVIDER=sarvam), so a
# change made only in gemini.py changes nothing the operator ever sees -
# which is exactly what happened on 2026-09-22.
FIRST_EMAIL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "company_paragraphs": {"type": "array", "items": {"type": "string"}},
        "could_be_a_user": {"type": "boolean"},
    },
    "required": ["subject", "company_paragraphs", "could_be_a_user"],
    "additionalProperties": False,
}

# Task 9: JSON-schema (lowercase) mirror of `gemini.CLASSIFY_SCHEMA` — same
# three fields, same six-label enum, `additionalProperties: false` for
# strict mode.
REPLY_CLASSIFICATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["label", "confidence", "rationale"],
    "additionalProperties": False,
}

# Task 13: JSON-schema (lowercase) mirror of `gemini.CRITIC_SCHEMA` — same
# three fields, `additionalProperties: false` for strict mode.
CRITIC_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "NEEDS_REVISION"]},
        "concerns": {
            "type": "array",
            # spec: "0-3 items". NOT enforced here via `maxItems` (fix
            # round 2, ruling T13-3): OpenAI-style strict `json_schema` mode
            # (which this endpoint follows) excludes `maxItems` from its
            # supported keyword subset, and Sarvam's own behaviour on it is
            # unconfirmed — untestable offline since every adapter test
            # here is mocked. The 0-3 bound is enforced
            # provider-independently in `run_critic`
            # (`app.application.critique_draft`) instead, by truncation.
            "items": {
                "type": "object",
                "properties": {
                    "span": {"type": "string"},
                    "issue": {"type": "string"},
                },
                "required": ["span", "issue"],
                "additionalProperties": False,
            },
        },
        "overall_note": {"type": "string"},
    },
    "required": ["verdict", "concerns", "overall_note"],
    "additionalProperties": False,
}

REVISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "revisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "replacement": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["index", "replacement", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["revisions"],
    "additionalProperties": False,
}

INSIGHT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "paragraph": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["paragraph", "why"],
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
            "max_tokens": _MAX_TOKENS,
            "reasoning_effort": "low",  # no-op on the conversations model; bounds the reasoning one
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
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise SarvamError(stage, status=status, detail="unexpected response shape") from None
        if not content:
            reason = choice.get("finish_reason")
            raise SarvamError(
                stage, status=status,
                detail=f"empty or truncated reply (finish_reason={reason}); a reasoning model "
                       f"may have spent max_tokens on reasoning_content — use {DEFAULT_MODEL}",
            )
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
        """Same split as `GeminiDrafter.draft`: step 0 writes only the
        company paragraph (the letter is assembled around it), a follow-up
        still writes the whole body."""
        first_email = int(prompt_context.get("sequence_step") or 0) == 0
        system = _FIRST_EMAIL_SYSTEM if first_email else _DRAFTER_SYSTEM
        schema = FIRST_EMAIL_JSON_SCHEMA if first_email else DRAFT_JSON_SCHEMA
        content = self._chat.complete(
            "drafter",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": build_draft_prompt(prompt_context)},
            ],
            schema,
            "outreach_draft",
            temperature=0.7,
        )
        data = _decode_json(content, "drafter")
        if not isinstance(data, dict):
            raise ValueError("drafter: expected a JSON object")
        if first_email:
            missing = [k for k in ("subject", "company_paragraphs") if k not in data]
            if missing:
                raise ValueError(f"drafter: response missing {', '.join(missing)}")
            kind, source_id = first_email_source(prompt_context)
            data["research_source_type"] = kind
            data["research_source_id"] = source_id
            return data
        missing = [k for k in ("subject", "body", "research_source_type",
                               "research_source_id") if k not in data]
        if missing:
            raise ValueError(f"drafter: response missing {', '.join(missing)}")
        return data


class SarvamReplyClassifier:
    """Real `ReplyClassifier` port (spec §5) over Sarvam. Same three-field
    contract as `GeminiReplyClassifier`, sharing the same prompt
    (`_CLASSIFIER_SYSTEM`/`build_classify_prompt`, imported from
    `app.adapters.gemini`) so both providers are held to exactly the same
    rules for what counts as each label."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, **chat_kwargs):
        self._chat = _SarvamChat(api_key, model, **chat_kwargs)

    def classify(self, context: dict) -> dict:
        content = self._chat.complete(
            "reply classifier",
            [
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": build_classify_prompt(context)},
            ],
            REPLY_CLASSIFICATION_JSON_SCHEMA,
            "reply_classification",
            temperature=0,
        )
        data = _decode_json(content, "reply classifier")
        if not isinstance(data, dict):
            raise ValueError("reply classifier: expected a JSON object")
        missing = [k for k in ("label", "confidence", "rationale") if k not in data]
        if missing:
            raise ValueError(f"reply classifier: response missing {', '.join(missing)}")
        try:
            confidence = float(data["confidence"])
        except (TypeError, ValueError) as exc:
            raise ValueError("reply classifier: confidence is not a number") from exc
        return {
            "label": str(data["label"]),
            "confidence": confidence,
            "rationale": str(data["rationale"]),
        }


class SarvamCritic:
    """Real `Critic` port (spec "The voice critic (D22)") over Sarvam. Same
    three-field contract as `GeminiCritic`, sharing the same prompt
    (`_CRITIC_SYSTEM`/`build_critic_prompt`, imported from
    `app.adapters.gemini`) so both providers judge a draft's voice against
    exactly the same rules. Advisory only — nothing here can block or
    approve a send."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, **chat_kwargs):
        self._chat = _SarvamChat(api_key, model, **chat_kwargs)

    def critique(self, draft: dict) -> dict:
        content = self._chat.complete(
            "voice critic",
            [
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user", "content": build_critic_prompt(draft)},
            ],
            CRITIC_JSON_SCHEMA,
            "voice_critique",
            temperature=0,
        )
        data = _decode_json(content, "voice critic")
        if not isinstance(data, dict):
            raise ValueError("voice critic: expected a JSON object")
        missing = [k for k in ("verdict", "concerns", "overall_note") if k not in data]
        if missing:
            raise ValueError(f"voice critic: response missing {', '.join(missing)}")
        return {
            "verdict": str(data["verdict"]),
            "concerns": data["concerns"],
            "overall_note": str(data["overall_note"]),
        }


class SarvamReviser:
    """Real `Reviser` port over Sarvam - the provider that actually runs
    (`LLM_PROVIDER=sarvam`). Same contract as `GeminiReviser`, sharing the
    same prompt (`_REVISER_SYSTEM`/`build_revise_prompt`, imported from
    `app.adapters.gemini`) so both providers rewrite a flagged span under
    exactly the same rules. Advisory only: nothing here changes a draft."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, **chat_kwargs):
        self._chat = _SarvamChat(api_key, model, **chat_kwargs)

    def revise(self, context: dict) -> list[dict]:
        content = self._chat.complete(
            "reviser",
            [
                {"role": "system", "content": _REVISER_SYSTEM},
                {"role": "user", "content": build_revise_prompt(context)},
            ],
            REVISION_JSON_SCHEMA,
            "revisions",
            temperature=0.3,
        )
        return _revisions(_decode_json(content, "reviser"), "reviser")


class SarvamInsightWriter:
    """Real `InsightWriter` port over Sarvam - the provider that actually
    runs (`LLM_PROVIDER=sarvam`). Same contract as `GeminiInsightWriter`,
    sharing the same prompt (`_INSIGHT_SYSTEM`/`build_insight_prompt`,
    imported from `app.adapters.gemini`) so both providers write the
    company paragraph under exactly the same rules. Advisory only:
    nothing here changes a draft."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, **chat_kwargs):
        self._chat = _SarvamChat(api_key, model, **chat_kwargs)

    def write(self, context: dict) -> dict:
        content = self._chat.complete(
            "insight",
            [
                {"role": "system", "content": _INSIGHT_SYSTEM},
                {"role": "user", "content": build_insight_prompt(context)},
            ],
            INSIGHT_JSON_SCHEMA,
            "insight",
            temperature=0.7,
        )
        return _insight(_decode_json(content, "insight"), "insight")
