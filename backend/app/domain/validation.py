# backend/app/domain/validation.py
"""Deterministic anti-slop rules. Spec §10. These are domain code, not prompt
instructions, precisely so they can be unit-tested and cannot be argued with."""
import re
from dataclasses import dataclass

from app.domain.names import company_display_name, company_name_variants

# Operator decision (2026-09-17): the tight caps were making every email
# sound clipped and machine-cut - the same shape every time, which is
# itself the tell. Room to write a real sentence matters more than
# brevity. These are now sanity bounds (nobody cold-emails a founder 200
# words), not a style device; rhythm is the prompt's job.
MAX_WORDS = 200
MAX_SENTENCE_WORDS = 45
MAX_PARAGRAPHS = 5
# Live finding (2026-09-13): the cap alone let a one-block email through —
# hook, pitch and ask run together in a single 3-sentence paragraph, which
# is exactly the wall-of-text the spec's three-part structure exists to
# prevent. Two is the floor (the ask may share a paragraph with the pitch);
# the prompt still asks for three.
MIN_PARAGRAPHS = 2
MAX_SENTENCES_PER_PARAGRAPH = 5
# Operator's template (2026-09-17) uses subjects like "SDE intern who's
# been poking around RoboGPT" - seven words. The rule exists to stop
# sprawling subject lines, not to force telegram-style ones.
MIN_SUBJECT_WORDS = 3
MAX_SUBJECT_WORDS = 10
MAX_EM_DASHES = 2

# Task 12 (spec §6): a follow-up is not just "shorter than the previous
# message" (that rule predates this task — see PF-3) but shorter on a
# shrinking schedule, one target per step, so the prompt can tell the model
# a concrete number instead of a vague "be brief". Step 1 (the first
# follow-up) allows more room than step 2 (the second/last), matching the
# existing shrink-rule's invariant that each step must be shorter than the
# one before it.
FOLLOW_UP_WORD_TARGETS = {1: 60, 2: 40}

# The near-restatement threshold reuses the exact ratio the one_liner-echo
# check below already uses (`hook overlaps more than 60% with one_liner`) —
# one shared notion of "too similar", not a second bespoke number.
FOLLOW_UP_RESTATEMENT_OVERLAP = 0.6

BANNED_PHRASES = [
    "i hope this email finds you well", "i am reaching out", "reaching out",
    "passionate about", "leverage", "synergy", "game-changing",
    "i came across your company", "delve", "tapestry", "in today's fast-paced",
    "reach out", "circle back", "at your earliest convenience",
    "i would love the opportunity", "please do not hesitate",
    # Task 12 (ruling T12-1): D29 bans naming a news source as a source
    # ("I saw the article") because the source is how you learned something,
    # never what the sentence is about. A follow-up's earlier email is the
    # same kind of channel, pointed at the sender instead of a publication —
    # "as I mentioned in my last email" is the identical tell. Deterministic
    # ban mirrors D29's SOURCE_REFERENCE_PHRASES below, one level up (the
    # channel is the sender's own prior message, not a research source).
    "my last email", "my previous email", "my earlier email", "my previous note",
    "as i mentioned", "in case you missed", "following up on my",
]
# Live finding (2026-09-16): the operator read a real draft and called it
# "very AI generated". The tells were praise and intensifiers ("feels like
# the exact interface small shops need"), not anything BANNED_PHRASES above
# caught. The "curious and specific" voice (gemini._DRAFTER_SYSTEM) tells
# the model not to write them; this list makes sure a draft that does is
# retried rather than shown to the operator. Substring matches, like the
# rest of BANNED_PHRASES, so "impressive" also catches "impressively".
# Multi-word tells only. Bare adjectives ("incredible", "impressive",
# "truly") are substring-matched and collide with company names the body is
# REQUIRED to contain (Incredible Health is a YC company) - a draft to them
# would fail every retry. The prompt and the critic cover single-word praise.
AI_TELL_PHRASES = [
    "feels like the exact", "the exact interface", "genuinely",
    "resonates with", "aligns with", "i'm excited", "so impressive",
    "truly impressive", "fascinating", "i was thrilled",
]
BANNED_PHRASES += AI_TELL_PHRASES
# Operator decision (2026-09-17): every cold email claims these, so they
# carry no information and read as filler - the project and the question
# are the evidence. Cut from the operator's own template on their say-so.
SELF_PRAISE_PHRASES = [
    "i learn fast", "learn quickly", "quick study", "quick learner",
    "comfortable figuring things out", "without much hand-holding",
    "without hand-holding", "work well independently", "self-starter",
    "i'd enjoy working on", "i would enjoy working on", "hard-working",
    "i'm a fast learner", "eager to learn",
]
BANNED_PHRASES += SELF_PRAISE_PHRASES
SENIORITY_PHRASES = [
    "years of experience", "in my current role", "my team", "i led a team",
    "throughout my career",
]
LOCATION_PHRASES = ["remote", "relocate", "based in", "work from"]
INTERN_TOKENS = ["internship", "intern", "interning"]
MIN_CAPS_WORD_LEN = 3
MIN_CAPS_WORDS_TO_SHOUT = 2


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\S+", text) if w]


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _hook(body: str) -> str:
    """The first paragraph — spec's part 1 of the three-part structure. The
    hook-grounding checks score only this, not the whole body, so a later
    paragraph can't accidentally launder a source or one_liner term."""
    paras = _paragraphs(body)
    return paras[0] if paras else body


def validate_style(draft, previous_body: str | None = None) -> list[str]:
    errs: list[str] = []
    body = draft.body
    low = body.lower()

    if len(_words(body)) > MAX_WORDS:
        errs.append(f"body exceeds {MAX_WORDS} words")

    for s in _sentences(body):
        if len(_words(s)) > MAX_SENTENCE_WORDS:
            errs.append(f"a sentence exceeds {MAX_SENTENCE_WORDS} words: {s[:40]!r}")
            break

    paragraphs = _paragraphs(body)
    if len(paragraphs) > MAX_PARAGRAPHS:
        errs.append(f"more than {MAX_PARAGRAPHS} paragraphs")
    elif len(paragraphs) < MIN_PARAGRAPHS:
        errs.append(
            f"fewer than {MIN_PARAGRAPHS} paragraphs - the hook, what you built and "
            "the ask must not run together in one block (separate them with a blank line)"
        )
    for p in paragraphs:
        if len(_sentences(p)) > MAX_SENTENCES_PER_PARAGRAPH:
            errs.append(f"a paragraph exceeds {MAX_SENTENCES_PER_PARAGRAPH} sentences")
            break

    if ";" in body:
        errs.append("contains a semicolon")

    sents = _sentences(body)
    if sents:
        i_starts = sum(1 for s in sents if re.match(r"^I\b", s))
        if i_starts * 2 > len(sents):
            errs.append('more than half the sentences begin with "I"')

    for phrase in BANNED_PHRASES:
        if phrase in low:
            errs.append(f"banned phrase: {phrase!r}")
    for phrase in SENIORITY_PHRASES:
        if phrase in low:
            errs.append(f"implies seniority: {phrase!r}")
    for phrase in LOCATION_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", low):
            errs.append(f"raises location: {phrase!r}")

    if not any(t in low for t in INTERN_TOKENS):
        errs.append("no explicit internship ask")

    dash_count = body.count("—") + body.count("–") + body.count(" -- ")
    if dash_count > MAX_EM_DASHES:
        errs.append("more than two em dashes")

    subject_words = _words(draft.subject)
    if not MIN_SUBJECT_WORDS <= len(subject_words) <= MAX_SUBJECT_WORDS:
        errs.append(
            f"subject is not between {MIN_SUBJECT_WORDS} and {MAX_SUBJECT_WORDS} words"
        )
    caps_words = [w for w in subject_words if len(w) >= MIN_CAPS_WORD_LEN and w.isupper()]
    if len(caps_words) >= MIN_CAPS_WORDS_TO_SHOUT:
        errs.append("subject contains an all-caps word")
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", draft.subject):
        errs.append("subject contains emoji")

    if draft.sequence_step > 0 and previous_body is not None:
        if len(_words(body)) >= len(_words(previous_body)):
            errs.append("follow-up is not shorter than the previous message")
        else:
            # Task 12: shorter is not the same as different. A trimmed-down
            # copy of the previous message is still shorter by word count
            # but moves the thread nowhere — reject it the same way the
            # one_liner-echo check below rejects a hook that just parrots
            # the one_liner back, by term overlap rather than a second
            # from-scratch similarity metric (PF-3).
            body_terms = _terms(body)
            prev_terms = _terms(previous_body)
            if body_terms and len(body_terms & prev_terms) / len(body_terms) > FOLLOW_UP_RESTATEMENT_OVERLAP:
                errs.append("follow-up restates the previous message instead of moving the thread forward")

    return errs


SOURCE_REFERENCE_PHRASES = [
    "saw the", "read the", "read about", "the article", "the piece",
    "coverage", "featured in", "in the news", "press release", "came across",
    # Live finding (2026-09-17): a draft opened "In Orangewood's launch
    # post, you mention that ..." - it names the channel the fact came
    # from, which is exactly what D29 forbids, and the list above only
    # covered news-shaped wording. The other source kinds (launch posts,
    # job postings, the YC page, the company's own site) need the same ban.
    "launch post", "your post", "the post", "your launch", "you mention",
    "you mentioned", "your website", "your site", "job posting",
    "your listing", "the listing", "your yc page", "your profile",
    "your blog", "blog post", "announcement",
]
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "at", "with",
    "is", "are", "was", "were", "that", "this", "it", "its", "your", "you",
    "their", "from", "by", "as", "be", "has", "have", "we", "our", "i", "over",
}


@dataclass(frozen=True)
class SourceRow:
    kind: str
    text: str
    publication: str | None
    intern_appropriate: bool | None


_SHORT_TERM_ALLOWLIST = {"ai", "ml", "vr", "3d"}


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z0-9']+", text.lower())
            if w not in _STOPWORDS and (len(w) > 2 or w in _SHORT_TERM_ALLOWLIST)}


def validate_grounding(draft, source, company_name, one_liner, claims,
                       all_publications=()) -> list[str]:
    """Spec §10 grounding rules. `all_publications` (final fix wave, F16)
    is every `company_news.source` for this company, cited or not: the
    spec bans any of them appearing verbatim in the body, and none of
    their words may count as the distinctive term the hook shares with
    the cited row (D29) - a draft citing a launch must not pass grounding
    by naming the outlet of a *different* news row.

    The spec's "company-specific token" rule (a job title or a distinctive
    term from `one_liner`/`long_description`) has no separate check: it is
    merged into the shared-distinctive-term check below (ruling FR-8)."""
    # `claims` is accepted for signature stability but the resume-claims rule
    # itself lives in `validate_claims` below (Task 14), which `validate_draft`
    # composes in alongside this function.
    errs: list[str] = []
    body = draft.body
    low = body.lower()

    if source is None or draft.research_source_id is None:
        return ["research source does not resolve to a real row"]

    for phrase in SOURCE_REFERENCE_PHRASES:
        if phrase in low:
            errs.append(f"refers to the source as a source: {phrase!r}")

    publications = [p for p in (source.publication, *all_publications) if p]
    pub_terms: set[str] = set()
    for publication in dict.fromkeys(publications):
        pub_terms |= _terms(publication)
        if publication.lower() in low:
            errs.append("publication name appears in the body")

    hook = _hook(body)
    substantive = _terms(source.text) - pub_terms
    shared = substantive & _terms(hook)
    if not shared:
        errs.append("hook shares no distinctive term with the cited source")

    variants = company_name_variants(company_name) if company_name else []
    if variants and not any(v.lower() in low for v in variants):
        errs.append("does not name the company")

    if source.kind == "job_posting" and source.intern_appropriate is False:
        errs.append("cited job posting is not intern-appropriate")

    if one_liner:
        ol = _terms(one_liner)
        if ol and len(ol & _terms(hook)) / len(ol) > 0.6:
            errs.append("hook overlaps more than 60% with one_liner")

    return errs

# --- Resume-claims rule (spec §10: "asserts a skill, metric or role absent
# from resume_profile.claims"). Deferred from Task 9, implemented in Task 14;
# tightened in fix round 1 (F4/F5).
#
# Two checks, each scoped to where it is unambiguous:
#
#   1. Proper-noun check, *first-person* sentences only (the "about me"
#      material, spec §10 part 2): a capitalised token of 3+ characters that
#      is not the first word of its sentence (a technology, tool, product,
#      institution: "Rust", "Kafka", "Stanford") must appear as a whole
#      token in some claim's `text`/`source_span` or in `allowed_text`.
#      Whole-token membership, not substring — a "JavaScript" claim does not
#      license "Java" (F5a).
#   2. Number check, every sentence outside the hook paragraph plus every
#      first-person sentence anywhere: a number ("90%", "10,000", "2027")
#      must appear (comma-stripped) in a claim or in `allowed_text`. The
#      hook is exempt because its numbers are facts about the company and
#      the grounding check owns those; a fabricated metric about the
#      sender's own project is most naturally written in the third person
#      ("The scheduler cut wait by 90%"), so first-person scoping alone
#      missed the most damaging fabrication (F5b). Bare one-digit numbers
#      ("3 months") are ignored (F4).
#
# `allowed_text` is everything that is a fact about *them* rather than the
# sender: `validate_draft` passes the company name, one-liner, cited source
# text, and (via `extra_allowed_text`) whatever the caller adds — the
# drafting use case adds `long_description` and the founder's name, since
# the prompt hands the model both (F4). `_CLAIM_STOPWORDS` exempts the
# ordinary capitalised words an internship email legitimately contains
# (month/day names for availability, GitHub/LinkedIn for "my code is on").
#
# What it deliberately does NOT police: lower-case technology names
# ("python", "postgres"), two-letter abbreviations ("CS", "AI", "ML"),
# sentence-initial capitals (indistinguishable from ordinary capitalisation),
# contractions ("I've", "Madhav's"). A false positive costs a wasted retry;
# a miss is caught by the human reviewer (D5). The rule errs toward silence.

_FIRST_PERSON = re.compile(r"\bI\b|\bI'(?:m|ve|d|ll)\b|\b[Mm](?:y|e|ine)\b")
_PROPER_TOKEN = re.compile(r"^[A-Z][A-Za-z0-9+#]{2,}$")
_NUMBER = re.compile(r"\d[\d,]*")
_KNOWN_TOKEN = re.compile(r"[a-z0-9+#]+")
_MIN_NUMBER_DIGITS = 2
# The template's own fixed wording (2026-09-17). These are not claims
# about the sender - "SDE intern" is the role being asked for and
# "15-minute chat" is the ask itself - but validate_claims saw an unknown
# proper token and an unsupported number and rejected every draft.
TEMPLATE_VOCABULARY = "SDE intern internship 15-minute 15 minute chat resume attached"

_CLAIM_STOPWORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "github", "linkedin",
})


def _claim_field(claim, name: str) -> str:
    if isinstance(claim, dict):
        return str(claim.get(name) or "")
    return str(getattr(claim, name, "") or "")


def _numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def validate_claims(draft, claims, allowed_text=()) -> list[str]:
    """Reject a sentence that asserts a technology/tool/proper noun (first-
    person sentences) or a number (any sentence outside the hook, or any
    first-person sentence) found in no resume claim and not in
    `allowed_text`. One error per offending sentence, listing every
    unmatched token."""
    known_text = " ".join(
        [_claim_field(c, "text") + " " + _claim_field(c, "source_span") for c in claims]
        + [str(t or "") for t in allowed_text]
        + [TEMPLATE_VOCABULARY]
    ).lower()
    known_tokens = set(_KNOWN_TOKEN.findall(known_text)) | _CLAIM_STOPWORDS
    known_numbers = _numbers_in(known_text)

    hook_sentences = set(_sentences(_hook(draft.body)))

    errs: list[str] = []
    for sentence in _sentences(draft.body):
        first_person = bool(_FIRST_PERSON.search(sentence))
        unmatched: list[str] = []

        if first_person:
            words = [w.strip(".,;:!?()[]{}\"'") for w in sentence.split()]
            for index, word in enumerate(words):
                if index == 0 or not word:
                    continue
                # `_PROPER_TOKEN` requires 3+ characters, which is what exempts
                # two-letter abbreviations ("CS", "AI", "ML").
                if _PROPER_TOKEN.match(word):
                    if word.lower() not in known_tokens:
                        unmatched.append(word)

        if first_person or sentence not in hook_sentences:
            for number in _NUMBER.findall(sentence):
                digits = number.replace(",", "")
                if len(digits) < _MIN_NUMBER_DIGITS:
                    continue
                if digits not in known_numbers:
                    unmatched.append(number)

        if unmatched:
            errs.append(
                "asserts something found in no resume claim: "
                f"{', '.join(dict.fromkeys(unmatched))} in {sentence[:40]!r}"
            )
    return errs


def validate_draft(draft, source, company_name, one_liner, claims,
                   previous_body=None, extra_allowed_text=(), all_publications=()) -> list[str]:
    allowed = [company_name or "", one_liner or "", source.text if source else "",
               *extra_allowed_text]
    return (validate_style(draft, previous_body)
            + validate_grounding(draft, source, company_name, one_liner, claims,
                                 all_publications=all_publications)
            + validate_claims(draft, claims, allowed_text=allowed))
