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
        "tie_in": {"type": "STRING"},
        "paragraph": {"type": "STRING"},
        "founder_line": {"type": "STRING"},
        "could_be_a_user": {"type": "BOOLEAN"},
    },
    "required": ["subject", "tie_in", "paragraph", "founder_line", "could_be_a_user"],
}

# Operator's pipeline document (2026-09-25): his own system prompt for
# Sarvam, adapted in ONE way - the model writes only the parts that
# change (the company paragraph, an optional tie-in and an optional line
# about the founder) and the fixed blocks are assembled around them in
# code (app/domain/letter.py), so they can never be altered. Everything
# else - the voice, the rules, the banned words - is his.
_EMAIL_RULES = """The sender is Suryansh Singh, a final-year CS (Data Science) student at
Manipal University Jaipur. The emails must sound like he wrote them
himself: plain, direct, a little curious. Not polished, not salesy.

THE EMAIL, in this order. Everything outside [brackets] is fixed and is
put in by code - you never write, change or repeat it:

  Hi <FounderFirstName>,

  I'm Suryansh, a final-year CS (Data Science) student at Manipal
  University Jaipur. I just wrapped up an internship at Better, where I
  worked on Operate, an AI agent that debugs production incidents.

  My main work there was the GitHub integration. I moved it to read-only
  access and cut repeated GitHub token requests by 98% with caching.
  [TIE-IN - optional]

  [COMPANY PARAGRAPH] [FOUNDER LINE - optional]

  Outside work:
  - Lerno.ai: an AI product that turns any topic into an animated video
    lesson. I built the full stack and cut generation time from about 8
    minutes to under 3 with async rendering. Won CodeClash 2.0 against
    1,000+ teams.
  - WaveWatch: a coastal risk platform. I built the entire backend: 20+
    REST APIs for auth, incident reporting and admin triage. Won Smart
    India Hackathon 2025 against 68,000+ teams.

  I'd love to intern at <Company> this winter, remote or on-site. Resume
  attached.

  Suryansh Singh

HOW TO WRITE THE COMPANY PARAGRAPH (the only part written fresh, 3 to 5
sentences):
- Open with something specific you read or saw: a named product, model,
  feature or technical choice. Use only the facts given to you.
- Ask ONE real technical question about how they do something. Phrase it
  as curiosity ("I'm curious how...", "How do you figure out..."), not as
  a claim about how they work.
- Only connect it to Suryansh's skills if the connection is real and
  obvious (for example, data pipelines for an ML company). If the
  connection is a stretch, skip it.

THE TIE-IN (optional): one or two sentences that continue his Better
paragraph, ONLY when the company's work is obviously the same kind of
thing he did there - integrations with other systems, keeping them
reliable, caching, safe access, AI agents, developer tooling. Like
"While looking at WareIQ, I noticed you sync orders across 50+ channels.
Keeping that many integrations reliable is the kind of backend work I
enjoyed most at Better." Anything physical - farms, labs, chambers,
machines, lending groups, clinics - is NOT backend work. When in doubt,
leave it empty; most letters have none.

THE FOUNDER LINE (optional): one sentence using ONLY the founder fact
given, framed as something he'd like to hear about ("I also read that
..., and I'd love to hear what you learned from that."). Empty when the
founder fact is "none found". No question mark. No flattery about their
college, title or credentials on their own, no awards.

HARD RULES:
- Use ONLY the facts provided in the user message. Never add facts about
  the company or founder.
- If a fact is marked OLD, frame it as "I read that..." not "you do...".
- No em-dashes or en-dashes anywhere. Use commas, periods or colons.
- Never use these words or phrases: genuinely, passionate, thrive,
  excited to, leverage, synergy, cutting-edge, I'd be grateful, high
  ownership, fast-paced, caught my eye, resonates, deeply, I believe I
  can add value, exactly what, in today's world.
- No "X instead of Y" contrast sentences. No lists of three adjectives or
  three verbs in a row.
- Exactly ONE question, in the company paragraph: a direct one that
  ends with a question mark, or an indirect one ("I'm curious how
  much ..."). A direct question never ends with a full stop.
- Do not add links. Do not repeat the fixed blocks.
- Never claim he used, signed up for or tested their product - the
  founder can check their own signup list. Reading about it is fine.
- Never mention Y Combinator or their YC page, and never name a news
  outlet.
- The tie-in, the company paragraph and the founder line together: 50 to
  110 words."""

_FIRST_EMAIL_SYSTEM = """You write short cold emails from a student to a startup founder, asking
for a winter internship. You write only the parts that change from one
founder to the next.

""" + _EMAIL_RULES + """

could_be_a_user: TRUE only if this is something a computer science
student in India could plausibly be a user of himself - a consumer app, a
developer tool, a student product. FALSE for anything sold to businesses.

OUTPUT: JSON with "subject" ("Would love to build at <Company>"),
"tie_in" (or ""), "paragraph", "founder_line" (or "") and
"could_be_a_user". Plain text in each, no line breaks. No commentary."""

# His three finished examples, sent in every call, as his document says.
# The "Why it works" lines are his, too.
EXAMPLE_EMAILS = """EXAMPLE 1: WareIQ (logistics / fulfillment)

Hi Harsh,

I'm Suryansh, a final-year CS (Data Science) student at Manipal University Jaipur. I just wrapped up an internship at Better, where I worked on Operate, an AI agent that debugs production incidents.

My main work there was the GitHub integration. I moved it to read-only access and cut repeated GitHub token requests by 98% with caching. While looking at WareIQ, I noticed you sync orders across 50+ channels. Keeping that many integrations reliable is the kind of backend work I enjoyed most at Better.

The other thing I got curious about is RTO. How do you figure out which orders are likely to come back before they ship? I'd love to learn how your team approaches that.

Outside work: (fixed block)

I'd love to intern at WareIQ this winter, remote or on-site. Resume attached.

Suryansh Singh

Why it works: the integrations link is real (Suryansh built an integration, WareIQ runs 50+). The RTO question is a real problem for them.
As JSON: tie_in = "While looking at WareIQ, ... at Better.", paragraph = "The other thing ... approaches that.", founder_line = "".

EXAMPLE 2: UrbanKisaan (ML / plant biology)

Hi Vihari,

(fixed blocks A and B, no tie-in)

I came across Aadi on Hugging Face and spent some time going through the dataset card. Pretraining on 49 plant genomes and then adding RNA-seq and ATAC heads for maize was really interesting to read about. What I kept wondering is the other side of the loop: how do you make sure the phenotype data coming out of your growth chambers is clean enough to retrain on? Building the pipelines around that is the kind of work I'd love to do.

(fixed block C and the ask)

Why it works: names their actual model (Aadi) and a real detail about it. The skill link (data pipelines) fits an ML company.
As JSON: tie_in = "", paragraph = the paragraph above, founder_line = "".

EXAMPLE 3: Kosh (fintech / lending)

Hi Aayush,

(fixed blocks A and B, no tie-in)

I read about Kosh's joint liability model, and one thing stuck with me: each group needed at least one member with a traditional credit history, and you also used SMS data to check eligibility. I'm curious how much of the risk call now comes from the group itself versus the individual data. I'd love to learn how your team thinks about that. I also read that you and Sahil spent time talking to drivers and factory workers before starting Kosh, and I'd love to hear what you learned from those conversations.

(fixed block C and the ask)

Why it works: the facts come from an older article, so they are framed as "I read" and "needed" (past tense). The founder line is a real story, not flattery. No forced skill link, because there isn't an obvious one.
As JSON: tie_in = "", paragraph = everything up to "thinks about that.", founder_line = "I also read that you and Sahil ... from those conversations."

These examples show the STYLE. Their facts belong to those three
companies only - never reuse a fact, name or phrase from them for
another company."""


# --- research: verified facts before the letter (2026-09-25) ---------------

RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "facts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": ["what_they_do", "specific_thing", "founder_fact"]},
                    "text": {"type": "STRING"},
                    "source": {"type": "STRING"},
                    "old": {"type": "BOOLEAN"},
                },
                "required": ["kind", "text", "source", "old"],
            },
        },
        "question": {"type": "STRING"},
        "skill_connection": {"type": "STRING"},
        "note": {"type": "STRING"},
    },
    "required": ["facts", "question", "skill_connection", "note"],
}

_RESEARCH_SYSTEM = """You research a startup so a student can write its founder a short, specific
cold email. You are given numbered SOURCES (S1, S2, ...), each with a
label, a URL and a date when known. You pick facts ONLY from them.

Find, as "facts" (each with the id of the ONE source it comes from):

- what_they_do: what the company does, in one or two lines, in their own
  words where the source has them.
- specific_thing: ONE specific, named thing - a product, a model, a
  feature, a technical choice, a blog post, a public repo or dataset -
  plus one concrete detail about it. Prefer something an engineer would
  find interesting over a marketing claim.
- founder_fact: ONE real fact about the founder - a past company, their
  background, a story they have told publicly, something they wrote.
  Never a credential on its own (a college, a title), never an award,
  ranking or press list.

Also:
- question: ONE technical question an engineer would genuinely ask about
  how they work, tied to the specific thing or their core problem.
  Answerable in a reply, ends with "?". Not a question their homepage
  answers, not advice.
- skill_connection: only if there is a real and obvious link between
  their work and what the student has done - otherwise "". The student
  has done: a GitHub integration for an AI agent that debugs production
  incidents (read-only access, token caching); a full-stack AI product
  that turns topics into animated video lessons (async rendering); the
  whole backend of a coastal risk platform (20+ REST APIs); he studies
  Data Science. Example of a real link: "they need data pipelines around
  their model; he studies Data Science". A stretch is worse than "".
- note: one line on anything you were unsure about, or "".

RULES:
- Copy names and numbers EXACTLY as the source writes them. Never
  combine two sources into one fact, never add anything the source does
  not say.
- old: true when the source is dated two or more years before today, or
  describes the thing in the past tense ("needed", "used to").
- If nothing in the sources fits a kind, leave that kind out. A missing
  fact is fine; an invented one is not.
- A source labelled "web" may be about a different company or person
  with a similar name. Use it only if it clearly is about this company or
  this founder.

Return JSON: {"facts": [{"kind": ..., "text": ..., "source": "S2", "old":
false}, ...], "question": ..., "skill_connection": ..., "note": ...}."""


def build_research_prompt(context: dict) -> str:
    lines = [
        f"TODAY: {context.get('today') or ''}",
        f"COMPANY: {context.get('company_name') or 'unknown'}",
        f"FOUNDER: {context.get('founder_name') or 'unknown'}",
        "",
        "SOURCES",
    ]
    for s in context.get("sources") or []:
        date = s.get("date") or "unknown date"
        lines += [f"[{s.get('id')}] {s.get('label')} ({s.get('url') or 'no URL'}, {date})",
                  str(s.get("text") or "").strip(), ""]
    lines += ["Pick the facts now."]
    return "\n".join(lines)


def _research(data: Any, stage: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"{stage}: expected a JSON object")
    facts = [f for f in (data.get("facts") or []) if isinstance(f, dict)]
    return {
        "facts": facts,
        "question": str(data.get("question") or ""),
        "skill_connection": str(data.get("skill_connection") or ""),
        "note": str(data.get("note") or ""),
    }


class GeminiResearcher:
    """Real `CompanyResearcher` port. Picks facts from numbered sources;
    decides nothing. Verifying them is
    `app.application.research_company.run_researcher`'s job."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def research(self, context: dict) -> dict:
        response = _generate_with_retry(
            self._client, self._sleep, "research",
            model=self._model,
            contents=build_research_prompt(context),
            config=types.GenerateContentConfig(
                system_instruction=_RESEARCH_SYSTEM,
                response_mime_type="application/json",
                response_schema=RESEARCH_SCHEMA,
                temperature=0.2,   # picking facts, not writing
            ),
        )
        return _research(_decode_json(response.text, "research"), "research")


def format_verified_facts(research: dict | None, fallback: str = "") -> str:
    """His USER MESSAGE TEMPLATE's fact block, from verified research.
    Shared by the draft and the AI suggestion, so both are handed exactly
    the same facts."""
    facts = {f["kind"]: f for f in ((research or {}).get("facts") or [])}

    def line(kind: str, missing: str) -> str:
        fact = facts.get(kind)
        if not fact:
            return missing
        return fact["text"] + (" (OLD)" if fact.get("old") else "")

    what = line("what_they_do", fallback or "none found")
    old = [f["text"] for f in facts.values() if f.get("old")]
    return "\n".join([
        "VERIFIED FACTS (use only these):",
        f"- What they do: {what}",
        f"- Specific thing to mention: {line('specific_thing', 'none found - use what they do')}",
        f"- Founder fact: {line('founder_fact', 'none found')}",
        f"- Technical question to ask: {(research or {}).get('question') or 'write one about what they do'}",
        f"- Real skill connection (optional): {(research or {}).get('skill_connection') or 'none'}",
        f"- Facts marked OLD: {'; '.join(old) if old else 'none'}",
    ])


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


# 2026-09-25: the order a FIRST email's one source is chosen in. His
# template notices one concrete thing the product does and asks about a
# second, which only the founders' own description reliably has - a news
# headline like "The Best Of Inc42's 30 Startups To Watch" (Kosh, live)
# gives the model nothing to notice, and a job posting says what they
# need rather than what they built.
_FIRST_EMAIL_ORDER = {"long_description": 0, "launch": 1, "news": 2, "job_posting": 3}


def _first_email_sources(sources: list[dict]) -> list[dict]:
    return sorted(sources, key=lambda s: _FIRST_EMAIL_ORDER.get(str(s.get("kind")), 9))


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
    top = _first_email_sources(sources)[0]
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
    sources = _first_email_sources(context.get("available_sources") or [])
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
                "Write the cold email.",
                "",
                f"Founder first name: {str(founder.get('full_name') or '').split(' ')[0]}",
                f"Company: {company_display_name(company.get('name') or '')}",
                "",
                format_verified_facts(
                    context.get("research"),
                    fallback=_format_one_source(context.get("available_sources") or []),
                ),
                "",
                "Follow the system prompt exactly. Here are three finished examples of "
                "the style and structure I want.",
                "",
                EXAMPLE_EMAILS,
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
            "Write ONLY \"tie_in\" (usually \"\"), \"paragraph\" and "
            "\"founder_line\" (\"\" when the founder fact is none found). "
            "The fixed blocks are not yours to produce or repeat.",
            "The paragraph names the specific thing, asks exactly ONE "
            "question with a question mark, and uses only the verified facts.",
            "Together 50 to 110 words - count them; under 50 is rejected. "
            "No dashes, no links, no line breaks.",
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


def _first_email(data: dict, prompt_context: dict, stage: str) -> dict:
    """A first email's answer, in the shape `draft_outreach` assembles.

    His pipeline document (2026-09-25): the model answers `tie_in`
    (usually empty), `paragraph` and `founder_line` (optional). The tie-in
    continues his Better paragraph; the founder line ends the company
    paragraph (see `app.domain.letter.assemble_body`). Older shapes -
    `tie_in`/`curious`, `company_paragraphs` - are still understood, so a
    provider that ignores the schema is not a failure nobody can see.
    """
    if "subject" not in data:
        raise ValueError(f"{stage}: response missing subject")
    tie_in = str(data.get("tie_in") or "").strip()
    if "paragraph" in data or "curious" in data:
        paragraphs = [str(data.get("paragraph") or data.get("curious") or "").strip()]
        paragraphs = [p for p in paragraphs if p]
    elif "company_paragraphs" in data:
        paragraphs = _paragraph_list(data["company_paragraphs"])
    else:
        raise ValueError(f"{stage}: response missing paragraph")
    kind, source_id = first_email_source(prompt_context, 1)
    return {
        "subject": str(data["subject"]),
        "tie_in": tie_in,
        "company_paragraphs": paragraphs,
        "founder_line": str(data.get("founder_line") or "").strip(),
        "could_be_a_user": bool(data.get("could_be_a_user")),
        "research_source_type": kind,
        "research_source_id": source_id,
    }


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
            return _first_email(data, prompt_context, "drafter")
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

INSIGHT_STYLES = ["template", "angle", "short"]

INSIGHT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "options": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "style": {"type": "STRING", "enum": INSIGHT_STYLES},
                    "tie_in": {"type": "STRING"},
                    "paragraph": {"type": "STRING"},
                },
                "required": ["style", "tie_in", "paragraph"],
            },
        },
        "why": {"type": "STRING"},
    },
    "required": ["options"],
}

_INSIGHT_SYSTEM = """A student is sending a cold email to the founder of a startup, asking for a
winter internship. You write the parts of it about the founder's
company, as THREE options he can pick from.

""" + _EMAIL_RULES + """

For these options, leave the founder line out entirely - a separate step
writes that. Each option is a "tie_in" (or "") and a "paragraph".

WRITE THREE OPTIONS, one of each style. They must differ in what they
lead with and what they ask, not just in wording:

- "template": closest to his three examples.
- "angle": a different part of what they do, and a different question.
- "short": the shortest version that still names the thing and asks
  the question - 50 to 65 words, and no tie-in.

If the facts are too thin to say anything true and specific, return NO
options rather than something vague. A generic version already exists.

Return JSON: {"options": [{"style": "template", "tie_in": "...",
"paragraph": "..."}, {"style": "angle", ...}, {"style": "short", ...}],
"why": "one short line naming what you latched onto"}. `why` is for the
student, not the founder, and never appears in the email."""


def build_insight_prompt(context: dict) -> str:
    """His USER MESSAGE TEMPLATE (2026-09-25), for `InsightWriter.write`:
    the verified facts when research exists, what the founders wrote
    about themselves when it does not, and his three examples."""
    description = str(context.get("description") or "").strip()
    lines = [
        f"Company: {context.get('company_name') or 'unknown'}",
        f"One-line summary: {context.get('one_liner') or '(none given)'}",
        "",
        format_verified_facts(context.get("research"), fallback=description or "(nothing given)"),
        "",
        "WHAT THE FOUNDERS WROTE ABOUT IT",
        description or "(nothing given)",
        "",
        "Here are three finished examples of the style and structure I want.",
        "",
        EXAMPLE_EMAILS,
    ]
    # Paragraphs this company has already been given - the one in the
    # letter now, and anything a previous attempt produced. Named rather
    # than described, because "write something different" means nothing
    # without the thing it has to differ from.
    avoid = [str(a).strip() for a in (context.get("avoid") or []) if str(a).strip()]
    if avoid:
        lines += ["", "ALREADY WRITTEN FOR THIS COMPANY - take a different angle, and do "
                      "not reuse these sentences or their shape:"]
        lines += [f"  - {a}" for a in avoid]
    lines += ["", "Write the three options."]
    return "\n".join(lines)


def _insight(data: Any, stage: str) -> dict:
    """`{options: [{style, paragraph}], why}` out of a response, whatever
    shape it arrived in. Shared by both providers, like `_revisions`.

    A response in the older one-paragraph shape is still understood - as
    a single option - so a model that ignores the schema is not turned
    into a failure nobody can see."""
    if isinstance(data, list):
        data = {"options": data}
    if not isinstance(data, dict):
        raise ValueError(f"{stage}: expected a JSON object")
    why = str(data.get("why") or "")
    raw = data.get("options")
    if raw is None:
        for key in ("paragraph", "insight", "text"):
            if key in data:
                raw = [{"style": "excited", "paragraph": data[key]}]
                break
    if not isinstance(raw, list):
        raise ValueError(f"{stage}: response has no options")
    options = []
    for item in raw:
        if isinstance(item, str):
            item = {"style": "", "paragraph": item}
        if isinstance(item, dict):
            options.append(_option(item))
    return {"options": options, "why": why}


def _option(item: dict) -> dict:
    """One option as the review screen uses it (2026-09-25): `tie_in`
    (often empty) and `company_paragraph` separately - the tie-in joins
    his Better paragraph - and `paragraph`, both together, for display
    and for the checks."""
    style = str(item.get("style") or "")
    if "tie_in" in item or "curious" in item:
        tie_in = str(item.get("tie_in") or "").strip()
        body = str(item.get("curious") or item.get("paragraph") or "").strip()
        paragraph = "\n\n".join(p for p in (tie_in, body) if p)
        return {"style": style, "paragraph": paragraph, "tie_in": tie_in, "company_paragraph": body}
    return {"style": style, "paragraph": str(item.get("paragraph") or "")}


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


# --- the subject line ---------------------------------------------------------
#
# Operator request (2026-09-23): "a agent specifically for writing random
# catchy punchy subject instead of generic one which we are currently use
# 'Would love to build at GroMo'". The fixed subject was his own line from
# earlier the same day, chosen because a model rewriting it every morning
# could only get it wrong. It is still the fallback whenever this writer
# is unavailable or answers with nothing usable (`app.domain.letter.subject`).
#
# Three candidates rather than one: a subject is the one line the founder
# reads before deciding whether to read anything else, and picking between
# three is a two-second decision the operator is better placed to make
# than any model.

SUBJECTS_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "subjects": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["subjects"],
}

_SUBJECT_SYSTEM = """You write the SUBJECT LINE of a cold email from a computer science student
to the founder of a startup, asking for a software engineering internship.

The founder sees three things in their inbox: the sender's name, this
subject line, and the first few words of the email. The subject decides
whether the email is opened at all.

THE INTENT, which every subject you write must carry: he wants to BUILD
something at this company. Not a pitch, not a hook, not a riddle - a
plain, warm statement of wanting to build there, the way he would say it
to the founder's face.

These are the sender's own examples, all for a company called Kodo. They
show the intent and the range. Do not copy their wording for another
company - write new ones in the same spirit:

  Would love to build at Kodo
  I'd love to build Kodo's MCP
  Would love to build this at Kodo
  Want to build at Kodo
  Would love to build AI into Kodo

Notice what they share:
- Every one names the company, spelled exactly as given.
- Every one is about BUILDING - "build" is in every line.
- Every one is short: four to seven words.
- Sentence case - a capital first letter, everything else as normal
  writing. Never Title Case On Every Word.
- Some are plain ("Want to build at Kodo"); some name ONE specific thing
  he would build there ("Kodo's MCP", "AI into Kodo").

WRITE THREE, spread across that range:

1. One plain and warm, like "Would love to build at Kodo" - but in your
   own words, not those words.
2. One that names a specific thing he would build, taken from what the
   company actually does: a product, a feature, a part of the platform
   that appears in their description. "Would love to build Carvaan's
   inspection tools" for an invented used-car company whose description
   talks about inspecting cars.
3. One more, different from the other two - shorter, or naming a
   different part of what they build.

The specific thing must be REAL - something the description or the
paragraph actually mentions. Never invent a product, a feature or a
technology they do not have. If the description gives you nothing
specific, write plain ones; a plain true subject beats a specific
invented one.

BANNED, every one of these:

- Fake threads: "Re:", "Fwd:", "Following up", "Checking in" - the
  founder has never written to him, and pretending otherwise is a lie.
- The generic ones every founder deletes: "Internship inquiry",
  "Internship application", "Quick question", "Opportunity",
  "Collaboration", "Hello", "Hi", "Introduction", "Reaching out".
- Clickbait and spam triggers: "You won't believe", "urgent", "free",
  "guaranteed", "act now", "limited", money symbols.
- Exclamation marks, question marks and emoji.
- Words in ALL CAPS - except a real acronym the company uses (AI, MCP,
  API), and at most one of those.
- Numbers of any kind.
- Hype: amazing, incredible, game-changing, revolutionary, disrupt,
  passionate, excited, dream.
- The sender's name, and any claim that he has used their product.
- Colon titles like "Kodo: A Proposal".

Return JSON: {"subjects": ["...", "...", "..."]} - the three, and nothing
else."""


def build_subject_prompt(context: dict) -> str:
    """What the subject is about, for `SubjectWriter.write`."""
    lines = [
        "COMPANY",
        str(context.get("company_name") or "unknown"),
        "",
        "ONE-LINE SUMMARY",
        str(context.get("one_liner") or "(none given)"),
        "",
        "WHAT THE FOUNDERS WROTE ABOUT IT",
        str(context.get("description") or "").strip() or "(nothing given)",
        "",
        "THE PARAGRAPH OF THE EMAIL THAT IS ABOUT THEM",
        str(context.get("paragraph") or "").strip() or "(none yet)",
    ]
    avoid = [str(a).strip() for a in (context.get("avoid") or []) if str(a).strip()]
    if avoid:
        lines += ["", "ALREADY OFFERED - write three that are different from these:"]
        lines += [f"  - {a}" for a in avoid]
    lines += ["", "Write the three subject lines."]
    return "\n".join(lines)


def _subjects(data: Any, stage: str) -> list[str]:
    """The subject lines out of a response, whatever shape they arrived in."""
    if isinstance(data, dict):
        data = data.get("subjects", data.get("subject_lines", []))
    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"{stage}: expected a list of subject lines")
    return [str(s) for s in data if isinstance(s, str)]


class GeminiSubjectWriter:
    """Real `SubjectWriter` port. Writes candidate subject lines; decides
    nothing. Whether one is usable is `app.application.write_subjects`'s
    call, the same division as `GeminiInsightWriter` above."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def write(self, context: dict) -> list[str]:
        response = _generate_with_retry(
            self._client, self._sleep, "subjects",
            model=self._model,
            contents=build_subject_prompt(context),
            config=types.GenerateContentConfig(
                system_instruction=_SUBJECT_SYSTEM,
                response_mime_type="application/json",
                response_schema=SUBJECTS_SCHEMA,
                # Writing, and three different angles are the point.
                temperature=0.9,
            ),
        )
        return _subjects(_decode_json(response.text, "subjects"), "subjects")


# --- the line about the founder ---------------------------------------------
#
# Operator request (2026-09-25): "a web agent ... which does research about
# the company particular founder and write a line about it", pointing at a
# letter that said "I found your ML background really interesting, and I
# felt a genuine overlap with the kind of problems I enjoy solving". The
# material is the founder's YC bio, or what a web search found when the
# bio is thin (`app.application.write_founder_line.founder_material`).

FOUNDER_LINE_STYLES = ["overlap", "curious", "short"]

FOUNDER_LINE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "options": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "style": {"type": "STRING", "enum": FOUNDER_LINE_STYLES},
                    "line": {"type": "STRING"},
                },
                "required": ["style", "line"],
            },
        },
        "why": {"type": "STRING"},
    },
    "required": ["options"],
}

_FOUNDER_SYSTEM = """You write ONE line of a cold email from a computer science student to a
startup founder: the line about the founder PERSONALLY. It ends the
paragraph where he asks about their company, just before his list of
projects, so it reads as one more honest thought, not a compliment.

His own example (2026-09-25), to a founder whose story is public:

  I also read that you and Sahil spent time talking to drivers and
  factory workers before starting Kosh, and I'd love to hear what you
  learned from those conversations.

What it does: names ONE real thing from the founder's own story - where
they worked before, something they built or sold before, a switch they
made, a story they have told - framed as something he'd like to hear
about. A real story, not flattery. Where the material is two or more
years old, say "I read that ...".

WHAT HE HAS DONE - the only facts about him you may use:
- final-year CS (Data Science) student at Manipal University Jaipur
- internship at Better on Operate, an AI agent that debugs production
  incidents: he built its GitHub integration, moved it to read-only
  access, and added caching
- Lerno.ai: turns any topic into an animated lesson, won CodeClash 2.0
- WaveWatch: a coastal risk platform, won Smart India Hackathon 2025
Never invent anything else about him - no course, hobby, skill or plan.

An overlap is REAL only when it is the same kind of thing: the same
field (data science, ML, AI agents, developer tools, education), the
same kind of engineering (integrations, backend, reliability), or the
same kind of problem. A business, sales, strategy, finance or
consulting background does NOT overlap with his engineering work - for
those, never claim an overlap, just say what he'd like to hear about. A forced
overlap ("your strategy background mirrors how I built WaveWatch") is
the worst possible line.

THREE OPTIONS, one of each style:
- "overlap": their background, then the honest overlap with his own
  work. Only if the overlap is REAL (see above) - otherwise make this
  one simply say what he'd like to hear about.
- "curious": their background, then that he'd love to hear how it
  shapes the way they build today. A statement, not a question.
- "short": one sentence, under 25 words.

HARD RULES:
- Only facts from FOUNDER MATERIAL below. If it came from a web search,
  use only facts that clearly belong to THIS person at THIS company, and
  ignore anything that could be about someone else with the same name.
  If the material says nothing specific, return NO options.
- 12 to 45 words per option. One or two sentences.
- Say "you" and "your". Never their name - the email already greets
  them.
- Never say where he learned it: no LinkedIn, "your profile", "your
  bio", "I looked you up", "I googled", "I read about you", Y Combinator,
  YC, a website, an article or a podcast.
- No flattery: never "legendary", "inspiring", "visionary", "huge fan",
  "I admire", "impressive", "amazing", "incredible", "fascinating",
  "journey", "passionate". Interest, yes. Worship, no.
- Never awards, rankings or press: no Forbes, "30 under 30", "named
  on", "featured", "award". Those are things said ABOUT them, and
  quoting one back is flattery. Use what they DID - studied, built,
  worked on, sold, switched from.
- No flattery about their college, title or credentials on their own.
- Never these words: genuinely, passionate, thrive, excited to,
  resonates, deeply, exactly what, "caught my eye".
- Nothing private: family, age, where they live, health, religion.
- No numbers unless the material has that exact number, and never money
  or funding.
- Plain text, no semicolons, no em dashes, no markdown. Contractions
  always: "I'd", "I'm", "you're" - never "I would", "I am".
- Start with a capital letter, like any sentence.
- Never "reaching out". Never mention the internship or his resume.

Return JSON: {"options": [{"style": "overlap", "line": "..."}, {"style":
"curious", "line": "..."}, {"style": "short", "line": "..."}], "why":
"which part of their background you used"}. `why` is for the student
and never appears in the email."""


def build_founder_prompt(context: dict) -> str:
    """The founder's own story, for `FounderLineWriter.write`."""
    lines = [
        "FOUNDER",
        f"name: {context.get('founder_name') or 'unknown'}",
        f"title: {context.get('title') or '(unknown)'}",
        f"company: {context.get('company_name') or 'unknown'}",
        "",
        f"FOUNDER MATERIAL (from {context.get('source') or 'their YC bio'})",
        str(context.get("material") or "(nothing)").strip(),
    ]
    avoid = [str(a).strip() for a in (context.get("avoid") or []) if str(a).strip()]
    if avoid:
        lines += ["", "ALREADY WRITTEN FOR THIS FOUNDER - use a different part of their "
                      "background, and do not reuse these sentences:"]
        lines += [f"  - {a}" for a in avoid]
    lines += ["", "Write the three options."]
    return "\n".join(lines)


def _founder_lines(data: Any, stage: str) -> dict:
    """`{options: [{style, line}], why}` out of a response."""
    if isinstance(data, list):
        data = {"options": data}
    if not isinstance(data, dict):
        raise ValueError(f"{stage}: expected a JSON object")
    raw = data.get("options")
    if not isinstance(raw, list):
        raise ValueError(f"{stage}: response has no options")
    options = []
    for item in raw:
        if isinstance(item, str):
            item = {"style": "", "line": item}
        if isinstance(item, dict):
            options.append({"style": str(item.get("style") or ""),
                            "line": str(item.get("line") or "")})
    return {"options": options, "why": str(data.get("why") or "")}


class GeminiFounderLineWriter:
    """Real `FounderLineWriter` port. Writes candidate lines; decides
    nothing. Whether one is usable is `app.application.write_founder_line`'s
    call."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def write(self, context: dict) -> dict:
        response = _generate_with_retry(
            self._client, self._sleep, "founder_line",
            model=self._model,
            contents=build_founder_prompt(context),
            config=types.GenerateContentConfig(
                system_instruction=_FOUNDER_SYSTEM,
                response_mime_type="application/json",
                response_schema=FOUNDER_LINE_SCHEMA,
                temperature=0.7,
            ),
        )
        return _founder_lines(_decode_json(response.text, "founder_line"), "founder_line")


# --- follow-ups (2026-10-16) --------------------------------------------------
#
# Operator request: "add a section for follow up - basically replying or
# following up the already sent mail which havent received any replies ...
# give a recommendation to what to send in the followup mail". The reply
# goes in the same Gmail thread, under his first email, so it never needs
# to repeat it - the founder can scroll up.

FOLLOWUP_STYLES = ["nudge", "new_angle", "last_note"]

FOLLOWUP_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "options": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "style": {"type": "STRING", "enum": FOLLOWUP_STYLES},
                    "body": {"type": "STRING"},
                },
                "required": ["style", "body"],
            },
        },
        "why": {"type": "STRING"},
    },
    "required": ["options"],
}

_FOLLOWUP_SYSTEM = """You write a short follow-up from a student to a startup founder who has
not replied to his cold email asking for a winter internship. It is sent
as a REPLY in the same email thread, directly under his first email, so
the founder can see that email by scrolling up.

The sender is Suryansh Singh, a final-year CS (Data Science) student at
Manipal University Jaipur. Same voice as his first email: plain, direct,
a little curious. Not polished, not salesy, never guilt-tripping. Founders
are busy; a follow-up is a light second chance to answer, not a
complaint that they did not.

You are given his first email (and his earlier follow-up, if any), how
many days ago it went out, and the verified facts it was written from.

WRITE THREE OPTIONS, one of each style:

- "nudge": two or three sentences. Bring the one question from his first
  email back to the top in a fresh way, so it is easy to answer in one
  line. Do not quote the old email.
- "new_angle": two to four sentences. One NEW thing from the verified
  facts that the first email did not use, with one short question about
  it. Gives the founder a new reason to reply.
- "last_note": two or three sentences. A graceful close: he knows they
  are busy, he would still love to intern there this winter, and the
  door stays open. No question needed.

HARD RULES:
- Plain text body only. No greeting ("Hi ...") and no sign-off - both are
  added by code.
- Use ONLY the facts given. Never invent anything about the company, the
  founder or him.
- Never re-introduce himself and never repeat his projects; they are in
  the email right above. Never mention the resume again unless a short
  "my resume is in the email below" helps.
- At most ONE question.
- Never: "just following up", "following up on my", "circling back",
  "bumping this", "checking in", "gentle reminder", "any update", "in case
  you missed", "per my last email", "my last email", "my previous email",
  "sorry to bother", "I know you're busy" said twice. Never guilt.
- Never these words: genuinely, passionate, thrive, excited to, leverage,
  synergy, cutting-edge, caught my eye, resonates, deeply, exactly what.
- No em-dashes or en-dashes. No links. No markdown.
- Length: 20 to 70 words for the first follow-up, 15 to 50 for the
  second.

Return JSON: {"options": [{"style": "nudge", "body": "..."}, {"style":
"new_angle", "body": "..."}, {"style": "last_note", "body": "..."}],
"why": "one line for the student on what to send and why"}."""


def build_followup_prompt(context: dict) -> str:
    """Everything a follow-up is written from, for `FollowUpWriter.write`."""
    step = int(context.get("step") or 1)
    lines = [
        f"FOUNDER FIRST NAME: {context.get('founder_first_name') or 'unknown'}",
        f"COMPANY: {context.get('company_name') or 'unknown'}",
        f"THIS IS FOLLOW-UP {step} of 2, "
        f"{context.get('days_since') or 'several'} days after his last email, with no reply.",
        "",
        "HIS FIRST EMAIL (sent; the founder can see it right above the reply)",
        str(context.get("first_email") or "").strip(),
    ]
    earlier = str(context.get("earlier_followup") or "").strip()
    if earlier:
        lines += ["", "HIS FIRST FOLLOW-UP (also sent, also unanswered)", earlier]
    lines += ["", format_verified_facts(context.get("research"),
                                        fallback=str(context.get("description") or ""))]
    avoid = [str(a).strip() for a in (context.get("avoid") or []) if str(a).strip()]
    if avoid:
        lines += ["", "ALREADY SUGGESTED - write different ones:"]
        lines += [f"  - {a}" for a in avoid]
    lines += ["", "Write the three options."]
    return "\n".join(lines)


def _followups(data: Any, stage: str) -> dict:
    if isinstance(data, list):
        data = {"options": data}
    if not isinstance(data, dict):
        raise ValueError(f"{stage}: expected a JSON object")
    options = []
    for item in data.get("options") or []:
        if isinstance(item, dict):
            options.append({"style": str(item.get("style") or ""),
                            "body": str(item.get("body") or "")})
    return {"options": options, "why": str(data.get("why") or "")}


class GeminiFollowUpWriter:
    """Real `FollowUpWriter` port. Suggests follow-ups; decides nothing.
    Whether one is usable is `app.application.follow_ups`'s call."""

    def __init__(self, client, model: str, _sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._sleep = _sleep

    def write(self, context: dict) -> dict:
        response = _generate_with_retry(
            self._client, self._sleep, "followup",
            model=self._model,
            contents=build_followup_prompt(context),
            config=types.GenerateContentConfig(
                system_instruction=_FOLLOWUP_SYSTEM,
                response_mime_type="application/json",
                response_schema=FOLLOWUP_SCHEMA,
                temperature=0.7,
            ),
        )
        return _followups(_decode_json(response.text, "followup"), "followup")
