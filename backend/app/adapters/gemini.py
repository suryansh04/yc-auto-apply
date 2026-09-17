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
import logging
import time
from typing import Any, Callable

from google.genai import errors as genai_errors
from google.genai import types

from app.domain.names import company_display_name
from app.domain.replies import LABELS, MACHINE_LABELS
from app.domain.validation import FOLLOW_UP_WORD_TARGETS

logger = logging.getLogger(__name__)

# Live-run fix (2026-09-13): Gemini answered 503 UNAVAILABLE ("high demand")
# on two flash models within minutes. Those and 429 are transient: retry
# with 5s/10s/20s backoff through an injectable `_sleep`, then re-raise.
_TRANSIENT_STATUS_CODES = {429, 503}
_MAX_TRANSIENT_RETRIES = 3
_BASE_BACKOFF_SECONDS = 5.0


def _generate_with_retry(client, _sleep: Callable[[float], None], what: str, **kwargs):
    for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
        try:
            return client.models.generate_content(**kwargs)
        except genai_errors.APIError as exc:
            if exc.code not in _TRANSIENT_STATUS_CODES or attempt == _MAX_TRANSIENT_RETRIES:
                raise
            delay = _BASE_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning("%s: Gemini %s, retrying in %.0fs", what, exc.code, delay)
            _sleep(delay)

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

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def parse(self, raw_text: str) -> list[dict]:
        response = _generate_with_retry(
            self._client, self._sleep, "resume parser",
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
asking whether they would take on a software engineering intern. The voice is
CURIOUS AND SPECIFIC: the email opens with something the student actually
wants to know about how the company's product works, says one true thing
about the student, and asks. It must read like one specific person wrote it
to one specific founder. It must never read like a template, a press
summary, a CV, or a language model being polite.

STRUCTURE: three or four paragraphs, with a blank line (two newline
characters) between them. Never run the parts together into a single
block. Write to be read in under a minute - usually 90 to 130 words - but
do not pad or clip a sentence to hit a number. Say the thing properly and
stop. No semicolons. At most two em dashes. Not more than half the
sentences may begin with "I".

RHYTHM matters more than length. A real person writes an uneven email: a
long thinking-out-loud sentence, then a short one. Three sentences of the
same length in a row is the clearest sign a machine wrote it. Let a
sentence run long when the thought is genuinely long, and let the next one
be four words.

Part 1 - the question (one to four sentences): a genuine, specific
question, or a concrete observation, about a MECHANISM or TRADE-OFF in the
thing they build - the kind of thing a curious engineer would actually ask
the person who built it. Where does the system decide X? What happens when
the input is ambiguous? Which part is the hard bit, A or B? Ground it in
exactly one of the AVAILABLE SOURCES below and name the specific product,
feature or problem from that source. It is fine for the question to be a
bit blunt. What part 1 must NEVER be: praise; a summary of the company
back to them; an abstract-noun description of what they do ("that natural
language layer", "the interface small shops need"); or a restatement of
the one-liner. If a sentence could be sent to any company by swapping the
name, delete it.

Part 2 - one or two sentences about you: that you are a student, the ONE
concrete thing you built (from the RESUME CLAIMS below) with its result,
and the words "resume attached". If a second sentence earns its place, it
says WHY that project connects to the question you just asked - not a
claim that your skills "fit", but the actual overlap in the problem. Skip
it when there is no honest connection. Do not list roles, tools, degrees or a second
project. Never write a sentence whose only content is which technologies
you know ("I know Python and React"), and never claim a skill "maps well"
to their work. Use ONLY the resume claims listed. Never invent a skill,
tool, metric, role or institution that is not in the claims. Never imply
professional seniority (no "years of experience", "my team", "in my
current role").

Part 3 - the ask (one plain sentence, ALWAYS present, its own final
paragraph): ask whether they would take a software engineering internship /
intern. Never end the email without this question. Speculative, not an
application to an advertised role. The words "intern" or "internship" must
appear.

TONE EXAMPLE - tone only. Every fact in your email must come from the
sources and claims given below, never from this example:

  Quick question about RoboGPT: when someone gives a vague prompt like
  "pick the red ones", where does the system push back - in the language
  model, or in the planner underneath? That boundary is the part I keep
  turning over.

  I'm a student looking for a software engineering internship, and the
  closest thing I've built is an AI grading pipeline for WaveWatch that
  cut manual work by about 70%. Same shape of problem, in a way: deciding
  when the model is confident enough to act on its own. Resume attached.

  Would Orangewood have room for an intern this year?

ANTI-TELLS - the things that make an email read as machine-written:
- Contractions are fine and preferred ("I'm", "I've", "doesn't").
- No stacked adjectives ("a powerful, intuitive, seamless tool").
- Never "genuinely", "truly" or "really" as intensifiers.
- Never "exactly the", "the exact", "feels like the exact".
- Never "resonates", "aligns", "excited", "thrilled", "impressive",
  "fascinating", "incredible".
- No sentence that summarises the company back to them - they know what
  they build.
- Vary sentence length. Three tidy sentences of the same shape in a row
  is a tell. A short one after a long one is fine.
- No praise. Interest is shown by the question, never stated.

THE SOURCE IS HOW YOU LEARNED IT, NEVER WHAT THE SENTENCE IS ABOUT. The
research source (a news headline, a launch post, a posting, a description)
tells you a fact; it then disappears. Write about the thing the company
built, never about the coverage of it. Never name the outlet or publication,
never give a date, never write "I saw", "I read", "the article", "the
piece", "came across", "featured in" or anything else that refers to the
source as a source. This applies to EVERY source kind, not just news: never
"your launch post", "the post", "you mention", "your website", "your job
posting", "your YC page", "your blog". You know the fact. Where you got it
is your business, not a sentence in the email.

NEVER mention location, remote work, relocation, or where you are based.
Never use: "I hope this email finds you well", "I am reaching out", "reach
out", "passionate about", "leverage", "synergy", "game-changing", "I came
across your company", "delve", "in today's fast-paced", "circle back", "at
your earliest convenience", "I would love the opportunity", "please do not
hesitate".

Name the company once in the body (not only in the subject).

FOLLOW-UPS: when the prompt below includes a THREAD SO FAR section, you are
writing a follow-up, not a first email - the founder already saw an earlier
note from you. Do not repeat what that note already said; assume they read
it and refer back to it in half a sentence at most ("following up on ...",
never a restatement of the whole hook). Never call that earlier note an
"email" or "message" you sent - the same rule that bans naming a news
article as a source applies to naming your own earlier note as a channel:
never write "my last email", "my previous email", "as I mentioned", "in
case you missed" or anything else that names the earlier note as a thing
you sent, rather than a thing you're continuing. Hit the word target given
for this step - it is lower than a first email's, on purpose, because a
follow-up earns less of the founder's attention than the first message
did, not more. The follow-up must end up shorter than the message before
it, every time.

THREAD SO FAR marks each entry "you sent" or "they replied" - or, for an
inbound row that is an automated reply (marked "automated reply, NOT the
founder", with its label - AUTO_REPLY or OUT_OF_OFFICE), that entry is a
bot, not the founder. Only acknowledge an entry marked "they replied" as
something the founder said. Never thank a founder for replying, and never
write as if they personally responded, when the only inbound entry is
marked as an automated reply - you may use it for timing only (for
example, they mentioned being away until a date).

OUTPUT: JSON with "subject" (three to six words, no emoji, no all-caps
words, naming the company), "body" (plain text, no markdown, no HTML, NO
greeting line and NO sign-off - those are added at send time), and
"research_source_type" / "research_source_id" naming exactly the one
source from AVAILABLE SOURCES that part 1 is grounded on.
"""


def _format_sources(sources: list[dict]) -> str:
    lines = []
    # Fix round 1 (F10): the publication name is deliberately NOT rendered.
    # D29 says the body must never name the outlet, so handing it to the
    # model only creates the temptation; nothing in the draft needs it.
    for s in sources:
        text = str(s.get("text", "")).replace("\n", " ").strip()
        lines.append(f"- kind={s.get('kind')} id={s.get('id')}: {text}")
    return "\n".join(lines) if lines else "- (none)"


def _format_claims(claims: list[dict]) -> str:
    lines = [f"- [{c.get('kind')}] {c.get('text')}" for c in claims]
    return "\n".join(lines) if lines else "- (none)"


def _format_thread_history(history: list[dict]) -> str:
    # Fix round 1 (ruling T12-2): an inbound row's `label` distinguishes a
    # real human reply from a bot. Only a machine label gets the
    # "automated reply" wording — a human label can never actually reach
    # here (the state machine moves the outreach to REPLIED first), and an
    # unclassified row (label=None) is treated as "they replied" rather
    # than silently hidden, since hiding it would be worse if it does turn
    # out to be human content.
    lines = []
    for m in history:
        body = str(m.get("body", "")).replace("\n", " ").strip()
        if m.get("direction") == "outbound":
            who = "you sent"
        elif m.get("label") in MACHINE_LABELS:
            who = f"automated reply, NOT the founder ({m.get('label')})"
        else:
            who = "they replied"
        lines.append(f"- [{who}, step {m.get('sequence_step')}]: {body}")
    return "\n".join(lines) if lines else "- (none)"


def build_draft_prompt(context: dict) -> str:
    company = context.get("company") or {}
    founder = context.get("founder") or {}
    parts = [
        "COMPANY",
        f"name: {company_display_name(company.get('name') or '')}",
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
    # Task 12: a follow-up (sequence_step > 0) gets its own section naming
    # the word target for this step and showing the thread so far — a
    # first email (step 0, or a context with no `sequence_step` at all)
    # gets neither, so `build_draft_prompt` renders identically to before
    # this task for step-0 calls.
    step = int(context.get("sequence_step") or 0)
    if step > 0:
        target = FOLLOW_UP_WORD_TARGETS.get(step, min(FOLLOW_UP_WORD_TARGETS.values()))
        parts += [
            "",
            f"THIS IS A FOLLOW-UP (step {step}). Do not repeat the earlier note - refer",
            "to it briefly instead. Target about "
            f"{target} words this time; it must end up shorter than the message before it.",
            "",
            "THREAD SO FAR",
            _format_thread_history(context.get("thread_history") or []),
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

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def draft(self, prompt_context: dict) -> dict:
        response = _generate_with_retry(
            self._client, self._sleep, "drafter",
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


# --- Task 9: GeminiReplyClassifier ---------------------------------------------

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "label": {"type": "STRING", "enum": list(LABELS)},
        "confidence": {"type": "NUMBER"},
        "rationale": {"type": "STRING"},
    },
    "required": ["label", "confidence", "rationale"],
}

_CLASSIFIER_SYSTEM = """You are classifying a founder's reply to a cold internship enquiry a
student sent them. Judge the founder's INTENT in the reply, never facts -
your job is to say what kind of reply this is, not whether anything in it
is true.

Label the reply with EXACTLY ONE of:
- INTERESTED: the founder wants to continue the conversation - asks a
  question, wants to talk, asks for a resume, proposes a time.
- REJECTED: the founder says no, is not hiring interns, or is not
  interested.
- REFERRAL: the founder points the student to someone else, a form, or a
  different channel to apply through, rather than answering directly.
- AUTO_REPLY: an automated acknowledgement - a bot confirming the message
  was received, a "we will get back to you" auto-responder, or anything
  else a human did not personally write in response to THIS message.
- OUT_OF_OFFICE: an automated out-of-office / vacation responder.
- UNCLEAR: anything that does not clearly fit one of the above - including
  a reply too short, too ambiguous, or too generic to judge with
  confidence on its own.

AUTO_REPLY and OUT_OF_OFFICE are for machine-generated replies ONLY. An
autoresponder must never be mistaken for a human decision: if you are not
reasonably sure a human personally wrote the reply, do not guess
INTERESTED or REJECTED - use UNCLEAR instead.

You will be shown the message the student originally sent and the
founder's reply to it - use the original message to understand what the
reply is actually responding to; a short reply like "Sure, Friday works"
is only classifiable in that context.

Return a JSON object with "label" (one of the six above, exactly as
spelled), "confidence" (a number from 0 to 1), and "rationale" (at most
25 words, explaining the label).
"""


def build_classify_prompt(context: dict) -> str:
    our_message = context.get("our_message") or ""
    reply_body = context.get("reply_body") or ""
    return (
        "OUR MESSAGE (what the student originally sent)\n"
        f"{our_message}\n\n"
        "THEIR REPLY (what you are classifying)\n"
        f"{reply_body}\n"
    )


class GeminiReplyClassifier:
    """Real `ReplyClassifier` port (spec §5). `classify(context)` returns
    `{label, confidence, rationale}`; `app.application.classify_reply`
    normalises `label` before trusting it (never this adapter's job — the
    adapter only guarantees the shape the schema promised, same division
    of responsibility as `GeminiDrafter`/`GeminiResumeParser` above)."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def classify(self, context: dict) -> dict:
        response = _generate_with_retry(
            self._client, self._sleep, "reply classifier",
            model=self._model,
            contents=build_classify_prompt(context),
            config=types.GenerateContentConfig(
                system_instruction=_CLASSIFIER_SYSTEM,
                response_mime_type="application/json",
                response_schema=CLASSIFY_SCHEMA,
                temperature=0,
            ),
        )
        data = _decode_json(response.text, "reply classifier")
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


# --- Task 13: GeminiCritic (D22 voice critic) -----------------------------------

CRITIC_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": ["PASS", "NEEDS_REVISION"]},
        "concerns": {
            "type": "ARRAY",
            # spec: "0-3 items". NOT enforced here via `maxItems` (fix
            # round 2, ruling T13-3): Gemini's `response_schema` field is
            # documented to reject `maxItems` on ARRAY types in its legacy
            # OpenAPI subset, which would break structured-output
            # acceptance on every real call — untestable offline since
            # every adapter test here is mocked. The 0-3 bound is enforced
            # provider-independently in `run_critic`
            # (`app.application.critique_draft`) instead, by truncation.
            "items": {
                "type": "OBJECT",
                "properties": {
                    "span": {"type": "STRING"},
                    "issue": {"type": "STRING"},
                },
                "required": ["span", "issue"],
            },
        },
        "overall_note": {"type": "STRING"},
    },
    "required": ["verdict", "concerns", "overall_note"],
}

_CRITIC_SYSTEM = """You are a second opinion on a cold internship email a student is about to
send a startup founder. Another process already checked it mechanically -
word counts, banned phrases, and that every specific claim is grounded in
real material. Do not re-check any of that. Your only job is naturalness,
specificity and concision: does this read like one specific person wrote
it to one specific founder, or does it read generic, stiff, or like a
template with the blanks filled in?

The single most useful question: is any sentence here doing no work? A
draft can pass every mechanical rule and still contain a sentence that
adds nothing, restates something already said, or could be sent to any
company with the names swapped. That is what you are here to catch.

Two more things to look for, because they are the tells a human reader
notices first:
- Reads like a template / AI-generated: tidy parallel sentences of the
  same length and shape, praise ("impressive", "exciting"), or a sentence
  that summarises the company back to the founder who built it.
- Part 1 (the first paragraph) is not a real question or a specific
  observation about how their product works - it is a compliment, a
  restatement of what they do, or an abstract-noun summary ("that
  natural language layer") rather than something a curious engineer would
  actually ask.

Never judge or comment on whether a fact is true, whether a claim is
grounded, or word/sentence counts - those are already handled and are not
your concern.

Return a JSON object with:
- "verdict": "PASS" if the email reads specific and natural, "NEEDS_REVISION"
  if it does not.
- "concerns": 0 to 3 objects, each with "span" (the exact phrase you are
  flagging, quoted verbatim from the email) and "issue" (one short,
  specific reason, e.g. "this opener could be sent to any company", "no
  person actually writes 'the innovative work you're doing'"). Empty list
  when there is nothing to flag.
- "overall_note": at most 20 words, summarising your take for a human
  reviewer who will see it next to the draft.
"""


def build_critic_prompt(draft: dict) -> str:
    return (
        "SUBJECT\n"
        f"{draft.get('subject')}\n\n"
        "BODY\n"
        f"{draft.get('body')}\n"
    )


class GeminiCritic:
    """Real `Critic` port (spec "The voice critic (D22)"). `critique(draft)`
    returns `{verdict, concerns, overall_note}`; `DraftOutreach` (via
    `app.application.critique_draft.run_critic`) is the only caller and the
    only place that decides what the verdict means for the draft - this
    adapter only guarantees the shape the schema promised, the same
    division of responsibility as `GeminiDrafter`/`GeminiReplyClassifier`
    above. Advisory only (D22): nothing here can block or approve a send."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def critique(self, draft: dict) -> dict:
        response = _generate_with_retry(
            self._client, self._sleep, "voice critic",
            model=self._model,
            contents=build_critic_prompt(draft),
            config=types.GenerateContentConfig(
                system_instruction=_CRITIC_SYSTEM,
                response_mime_type="application/json",
                response_schema=CRITIC_SCHEMA,
                temperature=0,
            ),
        )
        data = _decode_json(response.text, "voice critic")
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
