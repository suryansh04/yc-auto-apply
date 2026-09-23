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
import re
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
        # Live finding (2026-09-17): asked for a single STRING body, the
        # model returned all four parts run together with no blank lines,
        # or stopped after the hook - and repeated the miss on the retry.
        # An ARRAY makes the structure the schema's job, not prose the
        # model may skim. `_to_draft` joins with blank lines.
        "body": {"type": "ARRAY", "items": {"type": "STRING"}},
        "research_source_type": {"type": "STRING", "enum": SOURCE_KINDS},
        "research_source_id": {"type": "INTEGER"},
    },
    "required": ["subject", "body", "research_source_type", "research_source_id"],
}

# --- first emails: the model writes ONE paragraph ----------------------

# Operator decision (2026-09-22). A first email is now ASSEMBLED
# (app/domain/letter.py), not generated: the opener, who he is, the Better
# internship, WaveWatch, Lerno.ai, the ask and the close are his own words
# and are pasted in unchanged. The model's whole job is the paragraph
# about the company that sits under "Now Here's the Important Part:".
#
# This is why the fabrication bugs are gone rather than merely discouraged:
# the model is no longer handed the sender's resume to paraphrase, so it
# cannot attribute one project's metric to another, and it is told to
# write about the company only.
FIRST_EMAIL_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "subject": {"type": "STRING"},
        "company_paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}},
        "could_be_a_user": {"type": "BOOLEAN"},
    },
    "required": ["subject", "company_paragraphs", "could_be_a_user"],
}

_FIRST_EMAIL_SYSTEM = """You write ONE part of a cold email from a computer science student to a
startup founder. The rest of the email is already written by the sender
himself and you will never see or change it: how he introduces himself,
what he has built, and what he is asking for.

Your paragraph sits directly after the list of things the sender has
built, and directly BEFORE this line, which he wrote and you must not
repeat:

  "That's the kind of problem I'd genuinely like to work on."

So your paragraph has to end on a PROBLEM or a CHOICE - something
"that's the kind of problem" can point back at. It is the part that
proves a real person looked at this specific company. Everything before
it was about the sender; your paragraph is the turn to the founder.

WHAT TO WRITE - one or two short paragraphs, nothing else:

Name the CHOICE they made that you can see from outside, and why it is
not the obvious one. Not what the company does - the founder knows that -
but the decision underneath it. The strongest version contrasts what they
did NOT do with what they did:

  "I came across Loop Health and what caught my attention was that you
  weren't just building another generic employee benefits product. You
  were thinking specifically about what India's workforce actually
  needs."

  "the thing that stuck with me is that you didn't try to build another
  shopping app. You built a layer that sits on top of where people
  already shop."

That is the shape to aim for: a thing they avoided, then the thing they
chose instead. Both halves must be visible in the SOURCE below.

Ground it in WHAT THEY SAID below, and in nothing else.

THE SOURCE IS HOW YOU LEARNED IT, never what the sentence is about.
Naming the founder's own channel is fine and wanted - their launch post,
their job posting, what they wrote - because that is where you actually
read it. But NEVER name the news outlet or publication that covered them,
and never write "the article", "the piece", "coverage", "featured in" or
a date. A third party's write-up is not the founder's own words.

Naming where you read it is OPTIONAL and only worth doing when it reads
as a natural clause - "in your launch post you said ...", "the role you
posted asks for ...". If it will not sit inside a real sentence, leave it
out and simply state what they chose. Never bolt the channel onto the end
of a sentence as a fragment: "...sits outside the sterile field - what
you wrote about the company" is broken English and worse than saying
nothing. Never write a bracketed citation, a number or an id.

HARD RULES:

- Write about THEM. Never a sentence about the sender's skills,
  education, projects or character - those are already in the email and
  repeating them wastes the founder's attention.
- NEVER claim to have signed up for, tried, used, downloaded or tested
  the product. You do not know whether he has, and the founder can check
  their own signup list. Say what is visible from outside.
- Do not say he has NOT tried it either. That is an apology for homework
  nobody asked for.
- No praise. Interest is shown by what you noticed, never stated. Never
  "impressive", "excited", "resonates", "aligns", "fascinating",
  "passionate about". ("I came across X and what caught my attention
  was..." is fine - it is how you found them, not praise.)
- Never restate the company's one-liner back to them.
- Name the company once.
- No semicolons. At most two em dashes. Contractions are good.
- Vary sentence length. Three sentences of the same shape in a row is the
  clearest sign a machine wrote it.

could_be_a_user: TRUE only if this is something a computer science
student in India could plausibly be a user of himself - a consumer app, a
developer tool, a student product. FALSE for anything sold to businesses,
to factories, to hospitals, to other founders as infrastructure. You are
judging THE COMPANY, not the sender: do not guess whether he has used it,
only whether someone like him could. When true, the sender is given a
blank to fill in with his own real experience; you never write that part.

SUBJECT: three to ten words, no emoji, no all-caps words, naming the
company or their product.

OUTPUT: JSON with "subject", "company_paragraphs" (a JSON ARRAY, one
plain-text string per paragraph, one or two entries, no markdown, no
greeting, no sign-off) and "could_be_a_user". Nothing else - and never a
citation of any kind in the letter. The founder is reading a letter, not
a paper.
"""


_DRAFTER_SYSTEM = """You write a cold email from a student to a startup founder, asking for a
software engineering internship. The email works by CONNECTION, not by
pitching: the student has felt the same problem the company is solving,
says so with a real example, and asks to work on it.

It must read like one specific person wrote it to one specific founder
after actually looking at what they built. Never a template, never a press
summary, never a CV, never a language model being polite.

STRUCTURE: five paragraphs, blank line between each. Never one block.
Usually 180 to 240 words - but do not pad or clip a sentence to hit a
number. Say the thing properly and stop. No semicolons. At most two em
dashes.

Part 1 - the shared problem. Name where you learned what the founder
said or built (their launch post, their job posting, what they wrote) and
what specifically it was - the problem they set out to solve, or why they
started building it. Then say you have run into that same problem
yourself, and give ONE real, concrete example of when. THE EXAMPLE MUST
COME FROM THE RESUME CLAIMS BELOW - a project you built, something you
worked on, a thing that took you two weekends. Never invent a situation,
a course, a club, a job or an anecdote that is not in the claims. If no
claim honestly connects to their problem, write part 1 about the problem
itself and skip the personal example rather than making one up.

Part 2 - what you noticed in the product. Name a specific product or
engineering CHOICE you can see from the outside - how they structured
something, what they left out, what they made simple - and why you
noticed it. NEVER claim to have signed up for, tried, used or tested the
product: you have not, and the founder can check. Do not say you have
NOT tried it either - no "I haven't signed up, but ..." - that is an
apology for homework you were never expected to do. Just say what you
noticed. Never praise in the
abstract: no "it feels like it was built by people who understand the
user" unless you name the exact thing that made you think so.

The lesson in part 3 must come from the SAME fact group as the project
you name there. Saying one project taught you what a different job taught
you is a lie even though both facts are true.

Part 3 - about you: your year, degree and college, then the ONE project
most relevant to them with what it does and its result or number. Then one
honest lesson that project taught you - a real difficulty, not a virtue
("how painful deduplication gets once two systems both retry") - and one
half-sentence on why that is relevant to what they are working on. You may
name the technologies you work with when they overlap the company's own.
Everything here comes from the RESUME CLAIMS. Never invent a skill, tool,
metric, role, institution or number. Never imply seniority:
no "years of experience", no "my team", no "in my current role".

NEVER write a sentence about your character or work habits - "I learn
fast", "I'm comfortable figuring things out", "I work well
independently". Every applicant writes those. The project and the problem
you have felt are the evidence.

Part 4 - the ask: you would like to join as an SDE / software engineering
intern, and the SPECIFIC area you want to work on, taken from what you
learned about them. One honest line on why this problem in particular.
The words "intern" or "internship" must appear.

Part 5 - the close: say so in the words "resume attached" or "resume is
attached", then ask for a concrete next step - a quick 15-minute chat.

TONE EXAMPLE - TONE ONLY, and shorter than yours will be. Every fact in
your email must come from the sources and claims given below. This example
is about a robotics company that is NOT the one you are writing to: never
reuse its words. If your email mentions a "planner", a "planning layer",
"pick-and-place" or "monitoring tools" and the company below is not about
those things, you have copied the example and must start again.

  I read your launch post about turning a new pick-and-place task into a
  sentence instead of a reprogramming job. That stuck with me because of
  <the real project or job from the FACT GROUPS below where you hit the
  same problem - name it, and say what actually went wrong>.

  I can't try a robot arm from my desk, but the choice I noticed is that
  RoboGPT takes the prompt and the vision model separately rather than
  fusing them.

  <here: your year, degree and college, the ONE project from the claims
  that is closest to their problem, what it does, its number, and the
  honest lesson THAT project taught you - never a lesson borrowed from a
  different project, and never this example's words.>

  I'd like to join Orangewood as an SDE intern and work on that planning
  layer. It's a problem I've actually hit, on a much smaller scale.

  Resume is attached. Would you be open to a quick 15-minute chat?

ANTI-TELLS - the things that make an email read as machine-written:
- Contractions are fine and preferred ("I'm", "I've", "doesn't").
- No stacked adjectives ("a powerful, intuitive, seamless tool").
- Never "genuinely", "truly" or "really" as intensifiers.
- Never "exactly the", "the exact", "feels like the exact".
- Never "resonates", "aligns", "excited", "thrilled", "impressive",
  "fascinating", "incredible".
- No sentence that summarises the company back to them.
- Vary sentence length. Three sentences of the same shape in a row is a
  tell. A short one after a long one is fine.
- No praise. Interest is shown by what you noticed, never stated.

THE SOURCE IS HOW YOU LEARNED IT. Naming the founder's OWN channel - their
launch post, their job posting, what they wrote - is fine and wanted: that
is where you actually read it, and saying so is what makes part 1 honest.
But NEVER name the news outlet or publication that covered them, and never
write "the article", "the piece", "coverage", "featured in" or a date. A
third party's write-up is not the founder's own words.

Name the company once in the body (not only in the subject).

RHYTHM matters more than length. A real person writes an uneven email: a
long thinking-out-loud sentence, then a short one. Three sentences of the
same shape in a row is the clearest sign a machine wrote it. Let a
sentence run long when the thought is genuinely long, and let the next one
be four words.

NEVER mention location, remote work, relocation, or where you are based.
Never use: "I hope this email finds you well", "I am reaching out", "reach
out", "passionate about", "leverage", "synergy", "game-changing", "I came
across your company", "delve", "in today's fast-paced", "circle back", "at
your earliest convenience", "I would love the opportunity", "please do not
hesitate".

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

OUTPUT: JSON with "subject" (three to ten words, no emoji, no all-caps
words, naming the company or their product), "body" (a JSON ARRAY, one
plain-text string per paragraph - no markdown, no HTML, NO greeting line
and NO sign-off, those are added at send time), and
"research_source_type" / "research_source_id" naming exactly the one
source from AVAILABLE SOURCES that part 1 is grounded on.
"""


# Our source-kind enum in the words a person would use. The raw values are
# database plumbing; a founder who reads "I saw that in your
# long_description" learns we have a schema.
# Second person, because the model pastes these labels into the letter
# verbatim. In the third person that produced "In what they wrote about
# the company you described AlgoSurg as..." - talking about the founder to
# the founder.
SOURCE_KIND_LABELS = {
    "news": "a news story about you",
    "launch": "your launch post",
    "job_posting": "a role you posted",
    "long_description": "what you wrote about the company",
}


def _format_one_source(sources: list[dict]) -> str:
    """The single best source, with no number and no kind, for a first
    email.

    Live finding (2026-09-22), three times over: whatever token the list
    uses to identify a source, this model pastes it into the letter -
    `kind=long_description`, then `[17]`, then `Source 1`. Telling it not
    to supplies another example of the thing to imitate. With one source
    and no label there is nothing to cite, and `first_email_source` knows
    the answer without asking.
    """
    if not sources:
        return "(none)"
    top = sources[0]
    text = str(top.get("text", "")).replace("\n", " ").strip()
    label = SOURCE_KIND_LABELS.get(str(top.get("kind")), "their research material")
    return f"This is {label}:\n{text}"


def _format_sources(sources: list[dict]) -> str:
    """Numbered, in the operator's language - never `kind=long_description`.

    Live finding (2026-09-22): shown the raw enum, the model pasted it
    straight into the letter ("I saw that in your description
    (kind=long_description id=19)"), failing validation and burning both
    attempts. Three earlier founders were discarded for the same error.
    Telling it not to only reduced the rate; the string has to not be
    there. `first_email_source` maps the number back.
    """
    lines = []
    # Fix round 1 (F10): the publication name is deliberately NOT rendered.
    # D29 says the body must never name the outlet, so handing it to the
    # model only creates the temptation; nothing in the draft needs it.
    for i, s in enumerate(sources, start=1):
        text = str(s.get("text", "")).replace("\n", " ").strip()
        label = SOURCE_KIND_LABELS.get(str(s.get("kind")), "their research material")
        # "Source 1", not "[1]": shown a bracketed list the model
        # imitated the format and wrote academic citations into the
        # letter ("...what you wrote about the company [17].").
        lines.append(f"- Source {i} - {label}: {text}")
    return "\n".join(lines) if lines else "- (none)"


def first_email_source(context: dict, index=1) -> tuple[str, int | None]:
    """The `(kind, id)` of the source a first email was grounded on.

    The model is shown exactly one source and never names it, so this is
    always the priority-ordered first. `index` remains for the follow-up
    path and for tests; anything out of range falls back to the best one.
    """
    sources = context.get("available_sources") or []
    if not sources:
        return "", None
    try:
        chosen = sources[int(index) - 1]
    except (TypeError, ValueError, IndexError):
        chosen = sources[0]
    source_id = chosen.get("id")
    try:
        source_id = int(source_id) if source_id is not None else None
    except (TypeError, ValueError):
        source_id = None
    return str(chosen.get("kind") or ""), source_id


def _format_claims(claims: list[dict]) -> str:
    """Claims grouped by the resume line they came from.

    Live finding (2026-09-17): as a flat list, the model wrote "I ran into
    that on WaveWatch, where I had to wire four monitoring tools together"
    - the wiring belonged to a different job and WaveWatch is a different
    project. Two true facts fused into one false sentence. Grouping by
    `source_span` shows which facts came from the same line of the resume,
    so the model can see they are separate things.
    """
    if not claims:
        return "- (none)"
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for c in claims:
        key = str(c.get("source_span") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)
    lines: list[str] = []
    for n, key in enumerate(order, start=1):
        lines.append(f"FACT GROUP {n} (one thing - never mix with another group):")
        for c in groups[key]:
            lines.append(f"  - [{c.get('kind')}] {c.get('text')}")
    return "\n".join(lines)


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
        *(
            [
                "WHAT THEY SAID (ground your paragraph on this, and nothing else)",
                _format_one_source(context.get("available_sources") or []),
            ]
            if int(context.get("sequence_step") or 0) == 0
            else [
                "AVAILABLE SOURCES (ground part 1 on exactly one; cite its kind and id;",
                "listed in priority order - prefer the first one that gives you something specific)",
                _format_sources(context.get("available_sources") or []),
            ]
        ),
        "",
    ]
    # The resume goes to the model only when it is actually writing about
    # the sender - i.e. a follow-up. A first email's model writes one
    # paragraph about the COMPANY, so handing it the resume would only
    # invite it to paraphrase facts that are already in the letter
    # verbatim. That is precisely how a Better metric ended up attributed
    # to WaveWatch.
    if int(context.get("sequence_step") or 0) > 0:
        parts += [
            "",
            "RESUME CLAIMS (the only things you may say about the sender).",
            "Each FACT GROUP is ONE thing the sender did. NEVER attribute a "
            "detail from one group to the project named in another - saying a "
            "project did what a different job did is a lie, even though both "
            "facts are true on their own.",
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
    # Live finding (2026-09-17): with the four-part structure described only
    # in the (long) system prompt, sarvam-105b-conversations returned the
    # hook alone and dropped parts 2-4 - twice, so the retry burned too. A
    # short checklist at the END of the user prompt, right before the
    # instruction to write, is the part a small model actually follows.
    if step == 0:
        parts += [
            "",
            "Write ONLY the paragraph about this company - one or two "
            "entries in \"company_paragraphs\". The sender's own "
            "introduction, what he has built and his ask are already "
            "written and are not yours to produce or repeat.",
            "Name the choice they made that is not the obvious one: what "
            "they did NOT do, then what they did instead. Both halves must "
            "be visible in the source you cite.",
            "No entry may contain a blank line. Name the company once.",
            "Set could_be_a_user by judging THE COMPANY: could a computer "
            "science student in India plausibly be a user of this himself?",
        ]
    parts += ["", "Write the email now."]
    return "\n".join(parts)


def _paragraph_list(raw) -> list[str]:
    """The model's paragraphs as a list, whatever shape it sent.

    The schema asks for an ARRAY, but a model that answers with one
    string must not be turned into a single run-on paragraph, and a list
    must never be `str()`-ed - that renders "['one', 'two']", brackets
    and quotes included, into the email.
    """
    if isinstance(raw, (list, tuple)):
        return [str(p).strip() for p in raw if str(p).strip()]
    text = str(raw or "").strip()
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _paragraph_text(raw) -> str:
    """The same paragraphs as one body, blank line between each."""
    return "\n\n".join(_paragraph_list(raw))


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
        """A first email and a follow-up are two different jobs.

        Step 0: the model writes only the paragraph about the company;
        `draft_outreach` assembles the letter around it from
        `app.domain.letter`. The returned dict carries
        `company_paragraphs` and `could_be_a_user` instead of a body.

        Step > 0: unchanged - a follow-up is short, bespoke and has no
        fixed blocks to assemble, so the model writes the whole body.
        """
        first_email = int(prompt_context.get("sequence_step") or 0) == 0
        system = _FIRST_EMAIL_SYSTEM if first_email else _DRAFTER_SYSTEM
        schema = FIRST_EMAIL_SCHEMA if first_email else DRAFT_SCHEMA
        response = _generate_with_retry(
            self._client, self._sleep, "drafter",
            model=self._model,
            contents=build_draft_prompt(prompt_context),
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        data = _decode_json(response.text, "drafter")
        if not isinstance(data, dict):
            raise ValueError("drafter: expected a JSON object")
        if first_email:
            missing = [k for k in ("subject", "company_paragraphs") if k not in data]
            if missing:
                raise ValueError(f"drafter: response missing {', '.join(missing)}")
            kind, source_id = first_email_source(prompt_context, 1)
            return {
                "subject": str(data["subject"]),
                "company_paragraphs": _paragraph_list(data["company_paragraphs"]),
                "could_be_a_user": bool(data.get("could_be_a_user")),
                "research_source_type": kind,
                "research_source_id": source_id,
            }
        missing = [k for k in ("subject", "body", "research_source_type",
                               "research_source_id") if k not in data]
        if missing:
            raise ValueError(f"drafter: response missing {', '.join(missing)}")
        try:
            source_id = int(data["research_source_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("drafter: research_source_id is not an integer") from exc
        return {
            "subject": str(data["subject"]),
            "body": _paragraph_text(data["body"]),
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


# --- GeminiReviser (the hover-card rewrite) -------------------------------------

REVISION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "revisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "replacement": {"type": "STRING"},
                    "why": {"type": "STRING"},
                },
                "required": ["index", "replacement", "why"],
            },
        },
    },
    "required": ["revisions"],
}

_REVISER_SYSTEM = """A student is about to send a cold email to a startup founder asking for a
software engineering internship. A mechanical checker has flagged some
exact spans of that email. Your only job is to rewrite the flagged spans.

You are given the whole email for context and a numbered list of flagged
spans. For each span you can genuinely improve, return the replacement
text for THAT SPAN ALONE - not the paragraph around it, not the whole
email. The replacement is pasted in where the span was, so it has to read
correctly in place: match the span's punctuation and capitalisation, and
do not wrap it in quotes.

Hard rules:
- Never invent a fact. You may only rearrange, cut or plainly restate
  what the span already says. If fixing the note would require knowing
  something the email does not contain, skip that span.
- Never add praise, and never summarise the company back to the founder
  who built it.
- Write the way the rest of the email is written: plain words, short
  sentences, first person, no corporate register. No em dashes, no
  semicolons, no "leverage", "passionate", "excited to", "reach out".
- Shorter is better. If the honest fix is to delete the span, return an
  empty replacement for it - but only ever for a short phrase. Never
  answer with an empty replacement for a span longer than one sentence:
  deleting a whole paragraph is not a rewrite, and the student would be
  left with a letter that says nothing about the company.
- Skip any span you cannot improve. A list with fewer entries than you
  were given is a correct answer, and so is an empty list. Never return a
  replacement that is the flagged text again.

Return a JSON object with "revisions": a list of objects, each with
"index" (the number of the span you are rewriting, exactly as given),
"replacement" (the new text for that span), and "why" (at most 15 words,
addressed to the student, saying what the rewrite fixes).
"""


def build_revise_prompt(context: dict) -> str:
    """The email plus the numbered flagged spans, for `Reviser.revise`."""
    spans = context.get("spans") or []
    lines = [
        "COMPANY",
        str(context.get("company_name") or "unknown"),
        "",
        "EMAIL",
        str(context.get("body") or ""),
        "",
        "FLAGGED SPANS",
    ]
    for span in spans:
        lines.append(f"[{span.get('index')}] {span.get('quote')!r}")
        lines.append(f"    the checker says: {span.get('note')}")
    return "\n".join(lines) + "\n"


def _revisions(data: Any, stage: str) -> list[dict]:
    """The `revisions` list out of a response, whatever shape it arrived
    in. Shared by both providers - strict JSON-schema mode requires a
    top-level object, so the list is always wrapped, but a model that
    answers with a bare list is still understood rather than being turned
    into a failed suggestion nobody can see."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("revisions")
        if not isinstance(items, list):
            items = next((v for v in data.values() if isinstance(v, list)), [])
    else:
        raise ValueError(f"{stage}: expected a JSON object")
    out = []
    for item in items:
        if not isinstance(item, dict) or "index" not in item or "replacement" not in item:
            continue
        try:
            index = int(item["index"])
        except (TypeError, ValueError):
            continue
        out.append({
            "index": index,
            "replacement": str(item["replacement"]),
            "why": str(item.get("why") or ""),
        })
    return out


class GeminiReviser:
    """Real `Reviser` port. Rewrites the spans the checks flagged; decides
    nothing about the draft. Whether a rewrite is worth showing - and
    whether it is even a rewrite rather than the flagged text again - is
    `app.application.suggest_fixes`'s call, the same division of
    responsibility as `GeminiCritic` above."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def revise(self, context: dict) -> list[dict]:
        response = _generate_with_retry(
            self._client, self._sleep, "reviser",
            model=self._model,
            contents=build_revise_prompt(context),
            config=types.GenerateContentConfig(
                system_instruction=_REVISER_SYSTEM,
                response_mime_type="application/json",
                response_schema=REVISION_SCHEMA,
                temperature=0.3,
            ),
        )
        return _revisions(_decode_json(response.text, "reviser"), "reviser")


# --- the company paragraph, written from the YC description --------------
#
# Operator request (2026-09-23): the AI suggestion tab on the company
# paragraph had nothing to offer, because the reviser only ever REWRITES a
# flagged span and this span is usually fine as English. What the operator
# actually wants there is a fresh paragraph written from the company's own
# YC description - the block of text the founders wrote themselves, which
# is already synced into `companies.long_description` (148 companies, 139
# of them non-empty, 542 characters on average).
#
# The gold-standard example below is the operator's own, quoted verbatim
# from the request, and the two after it are invented companies - a real
# one would teach the model to reach for that company's facts.

INSIGHT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "paragraph": {"type": "STRING"},
        "why": {"type": "STRING"},
    },
    "required": ["paragraph"],
}

_INSIGHT_SYSTEM = """A student is sending a cold email to the founder of a startup, asking for a
software engineering internship. Every other paragraph of that email is
already written and is about the student. You write ONE paragraph: the
only part of the letter that is about the founder's company.

Your one source is the description the founders wrote for their own Y
Combinator page, which you are given below along with the company name and
its one-line summary. You know nothing else about this company, and you
must not pretend to.

THE SHAPE, in three sentences:

1. What you noticed about the way they built it. Not what the product is -
   the CHOICE inside it. Start from the structure: who they put on the same
   side, what they refused to charge for, what they made unlimited, what
   they moved from one party to another.
2. How that problem normally works, and why the ordinary version is bad for
   the actual person living with it. Plain language, the way you would
   explain it to a friend.
3. What they did instead, and one short clause on why that is the kind of
   thing you want to build.

THE STANDARD. This is the paragraph to match, for a health insurance
company whose description says they combine primary care with insurance and
give employees unlimited clinic visits:

  I like how Loop puts doctors and insurers on the same side. Most health
  insurance in India only kicks in when you land in a hospital, so people
  pay for it every year and barely use it. You turned it into something
  people actually use day to day, and that's the kind of thing I want to
  build.

Notice what it does: one structural observation, one sentence about the
ordinary world, one about what they changed. It quotes no numbers. It
praises nothing. It reads like a person thinking out loud.

TWO MORE, for companies that are not health insurance:

  Company: Halt (invented), a tool that reviews infrastructure changes
  before they are applied.
  I like that Halt reads the change before it runs rather than paging
  someone after it breaks. Most teams find out their config was wrong from
  an outage in the middle of the night, which means the person who has to
  fix it is the person least able to think. Catching it while someone is still awake and looking
  at the diff is a much better place to put the work.

  Company: Kirana Books (invented), bookkeeping for small shop owners over
  WhatsApp.
  I like that you let shop owners keep their books in the app they already
  have open all day. Every other option asks a shopkeeper to learn
  accounting software in a language that is not theirs, so the ledger ends
  up back in a paper notebook. Meeting people inside the habit they already
  have is the harder engineering problem and the right one.

HARD RULES:

- Three sentences. Four only if the third would otherwise run long. One
  paragraph, 45 to 85 words.
- Plain text. No markdown, no bold, no bullets, no headings, no em dashes,
  no smart quotes, no semicolons.
- Address them as "you" and "your". Name the company at most twice, and
  never as "your company".
- Every fact comes from the description. If it does not say how something
  works, do not guess. Never invent a feature, a customer, a launch, a
  competitor, a funding round or a person.
- NO NUMBERS from the description. No revenue, growth rate, customer count,
  member count, market size, CAGR, pricing, margins, headcount or funding.
  Those are facts for investors, and repeating them reads as reciting their
  fundraising page back at them.
- Never mention Y Combinator, the YC page, their website, an article, a
  post, or how the student found them. No "I came across", "I saw", "I
  read", "while looking".
- Never claim to have used, signed up for, tested, bought or been a
  customer of the product.
- No praise words and no hype: impressive, amazing, incredible, exciting,
  excited, passionate, love what you are doing, game-changing,
  revolutionary, disrupting, cutting edge, best-in-class, "the X space",
  leverage, synergy, resonates, aligns, "truly".
- No questions. No advice about what they should build next. No criticism
  of their product, and no telling them what their own market is.
- Do not mention the internship, the student's skills, projects, resume or
  availability. Other paragraphs do that, and repeating it wastes the only
  paragraph that is about them.
- The last clause names what YOU would want to work on, and it has to be
  about THIS company. "which is the sort of problem I want to work on" and
  "that's the kind of thing I want to build" are formulas that fit any
  company, so they are worth nothing - end on the specific thing instead
  (the interface, the routing, the fact that the boring half is now a
  machine's job). Never copy the example's closing words.
- Write like a smart twenty-year-old talking, not like a brochure. Short
  words. Contractions are fine.

If the description is too thin to say anything true and specific - a single
marketing line with no mechanism in it - return an EMPTY paragraph rather
than something vague. A generic line is already written for that case, and
inventing a detail is far worse than having nothing to add.

Return JSON: {"paragraph": "...", "why": "one short line naming what you
latched onto"}. `why` is for the student, not the founder, and never
appears in the email."""


def build_insight_prompt(context: dict) -> str:
    """The company's own words, for `InsightWriter.write`."""
    description = str(context.get("description") or "").strip()
    lines = [
        "COMPANY",
        str(context.get("company_name") or "unknown"),
        "",
        "ONE-LINE SUMMARY",
        str(context.get("one_liner") or "(none given)"),
        "",
        "WHAT THE FOUNDERS WROTE ABOUT IT",
        description or "(nothing given)",
        "",
        "Write the paragraph.",
    ]
    return "\n".join(lines)


def _insight(data: Any, stage: str) -> dict:
    """The paragraph out of a response, whatever shape it arrived in.
    Shared by both providers, like `_revisions` above."""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise ValueError(f"{stage}: expected a JSON object")
    for key in ("paragraph", "insight", "text"):
        if key in data:
            return {"paragraph": str(data[key] or ""), "why": str(data.get("why") or "")}
    raise ValueError(f"{stage}: response has no paragraph")


class GeminiInsightWriter:
    """Real `InsightWriter` port. Writes the company paragraph from the
    company's own YC description; decides nothing about the letter.
    Whether the answer is usable is `app.application.write_insight`'s
    call, the same division as `GeminiReviser` above."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def write(self, context: dict) -> dict:
        response = _generate_with_retry(
            self._client, self._sleep, "insight",
            model=self._model,
            contents=build_insight_prompt(context),
            config=types.GenerateContentConfig(
                system_instruction=_INSIGHT_SYSTEM,
                response_mime_type="application/json",
                response_schema=INSIGHT_SCHEMA,
                # Higher than the reviser's 0.3: this is writing, not
                # correcting, and the same company asked twice should be
                # able to come back with a different angle.
                temperature=0.7,
            ),
        )
        return _insight(_decode_json(response.text, "insight"), "insight")
