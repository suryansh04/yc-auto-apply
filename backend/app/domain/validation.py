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
#
# Raised again (2026-09-22) for the operator's assembled letter: the fixed
# blocks in app/domain/letter.py are 193 words before the model writes a
# syllable, and the example email the operator supplied is 318. The cap
# leaves room for two company paragraphs and the operator's own user-fit
# paragraph on top of that. `test_letter.py` pins the cap against the
# real block sizes, so shortening the cap below what the blocks need
# fails a test instead of rejecting every draft at run time.
MAX_WORDS = 380
MAX_SENTENCE_WORDS = 45
# Seven fixed blocks, up to two generated company paragraphs, plus the
# operator's user-fit paragraph.
MAX_PARAGRAPHS = 11
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
MAX_I_START_RATIO = 0.7
_BRACKETED_CITATION = re.compile(r"\[\s*\d+\s*\]")

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
    # "genuinely" was here and came out (2026-09-22): a bare adverb, and
    # the operator uses it honestly in his own example email. Same
    # over-reach as the bare adjectives the comment above describes.
    "feels like the exact", "the exact interface",
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
    # Asserting relevance instead of showing it (live, 2026-09-17: "That is
    # relevant to what Mudrex is working on.").
    "is relevant to what", "which is relevant to", "relevant to what you",
]
BANNED_PHRASES += SELF_PRAISE_PHRASES

# Live finding (2026-09-17): told to claim it had tried the product ONLY
# where a student really could, the model wrote "I signed up and tried
# sending a text prompt to move an object" - to a ROBOT ARM company. A
# model cannot be trusted to judge its own honesty here and the founder
# can check their own signup list, so the claim is banned outright. What
# the sender noticed from the outside is always available and always true.
TRIED_IT_PHRASES = [
    "i signed up", "signed up and tried", "i tried it", "i've tried it",
    "i have tried it", "i used it", "i've used it", "i have used it",
    "i started using", "i've been using", "i have been using",
    "when i tried", "trying it out", "i gave it a go", "i tested it",
    "created an account", "made an account",
    # The negative form is honest but reads as an apology for homework not
    # done (live, 2026-09-17: "I have not signed up for Mudrex, but ...").
    # Just say what you noticed.
    "i have not signed up", "i haven't signed up", "i could not sign up",
    "i couldn't sign up", "i can't sign up", "i cannot sign up",
    "have not tried", "haven't tried",
]
BANNED_PHRASES += TRIED_IT_PHRASES

# Our internal source-kind names are plumbing, not English. "In your long
# description you said ..." (live, 2026-09-17) tells the founder we have a
# database schema, not that we read their words.
INTERNAL_SOURCE_NAMES = [
    "long description", "long_description", "one-liner", "one liner",
    "your description", "the description", "job_posting",
]
BANNED_PHRASES += INTERNAL_SOURCE_NAMES
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
    """The paragraph the grounding checks score - the one that has to earn
    its place by saying something true about THIS company.

    Historically that was the first paragraph. In an assembled letter
    (2026-09-22) the first paragraph is the operator's fixed opener, which
    is the same for every founder and could never share a term with a
    source; the paragraph that must be grounded is the one the model wrote,
    under "Now Here's the Important Part:". Scoring the opener instead
    rejected every draft with "hook shares no distinctive term with the
    cited source".

    Only that paragraph is scored, never the whole body, so a later
    paragraph still cannot launder a source or one_liner term.
    """
    from app.domain.letter import ASK, SECTION_BREAK

    if SECTION_BREAK in body:
        after = body.split(SECTION_BREAK, 1)[1]
        # Everything up to the fixed ask is the model's; the ask and the
        # close are the operator's again.
        company_part = after.split(ASK, 1)[0]
        paras = _paragraphs(company_part)
        if paras:
            return "\n\n".join(paras)
    paras = _paragraphs(body)
    return paras[0] if paras else body


def _generated_region(body: str, company_name: str) -> str:
    """`body` with the operator's fixed blocks removed - i.e. only the text
    the model actually produced.

    The blocks are exact strings (letter.fixed_blocks), so this is an exact
    removal, not a fuzzy one. A body that was not assembled from them comes
    back unchanged, which is the safe direction: every rule still applies.
    """
    from app.domain.letter import fixed_blocks

    remaining = body or ""
    for block in fixed_blocks(company_name or "Company"):
        remaining = remaining.replace(block, " ")
    return remaining


def validate_style(
    draft,
    previous_body: str | None = None,
    company_name: str = "",
    *,
    operator_edit: bool = False,
) -> list[str]:
    """`operator_edit` says this text was typed by the person who signs it.

    The phrase bans exist to stop the MODEL reaching for a tell; they are
    not a style review of the operator's own sentences. Rejecting his
    edit for a word he chose - "genuinely", "passionate about" - would be
    the tool arguing with its owner about his own voice. Everything
    structural (length, paragraphs, semicolons, seniority, location, an
    unfilled stub) still applies, because those are mistakes rather than
    taste.
    """
    errs: list[str] = []
    body = draft.body
    low = body.lower()
    generated_raw = _generated_region(body, company_name)

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

    # Live finding (2026-09-22): shown a numbered source list, the model
    # started appending academic citations - "That choice is visible in
    # what you wrote about the company [17]." The number is our own list
    # index (or an invented one); a founder reading it learns we have a
    # bibliography. Deterministic, because telling the model not to only
    # reduced the rate - the same lesson as the source-kind enum.
    if _BRACKETED_CITATION.search(generated_raw):
        errs.append("contains a bracketed citation like '[3]' - never cite by number")

    sents = _sentences(body)
    if sents:
        i_starts = sum(1 for s in sents if re.match(r"^I\b", s))
        # Operator's template (2026-09-17) is a first-person story - "I've
        # run into the same problem", "I signed up and tried it", "I
        # recently built". Half was too strict for it; the rule still
        # catches an email that is nothing but "I" sentences.
        if i_starts > len(sents) * MAX_I_START_RATIO:
            errs.append('too many sentences begin with "I"')

    # The phrase bans exist to stop the MODEL reaching for a tell. They are
    # not a review of the operator's own copy: the fixed blocks in
    # app/domain/letter.py are his words, pasted in unchanged, and two of
    # them would otherwise be rejected forever. So the bans run over the
    # body with those blocks removed - what is left is exactly what the
    # model wrote.
    generated = generated_raw.lower()
    if not operator_edit:
        for phrase in BANNED_PHRASES:
            if phrase in generated:
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
    # Live finding (2026-09-17): "SDE internship at Bulk MRO" was rejected as
    # shouting - two all-caps tokens, both legitimate (the role the operator
    # asks for, and the company's own name). Only tokens that are neither
    # count toward the rule.
    allowed_caps = {"SDE"} | {
        w.upper() for v in (company_name_variants(company_name) if company_name else [])
        for w in v.split()
    }
    caps_words = [
        w for w in subject_words
        if len(w) >= MIN_CAPS_WORD_LEN and w.isupper() and w.strip(".,") not in allowed_caps
    ]
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


# Operator decision (2026-09-17): D29's "never name the source" is LIFTED
# for first emails. Their template opens "I read your launch post about
# <what you said>, and it stuck with me because I've run into the same
# problem" - naming the channel is what makes that sentence honest rather
# than a claim to have divined the fact. What stays banned is naming the
# PUBLICATION (the outlet's coverage, not the founder's own words) and the
# passive "I came across" framing the operator rejected earlier.
SOURCE_REFERENCE_PHRASES = [
    "the article", "the piece", "coverage", "featured in", "in the news",
    "press release", "came across",
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
    generated_raw = _generated_region(body, company_name)

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


def validate_claims(draft, claims, allowed_text=(), company_name: str = "") -> list[str]:
    """Reject a sentence that asserts a technology/tool/proper noun (first-
    person sentences) or a number (any sentence outside the hook, or any
    first-person sentence) found in no resume claim and not in
    `allowed_text`. One error per offending sentence, listing every
    unmatched token.

    The operator's own fixed blocks (app/domain/letter.py) are exempt, for
    the same reason the phrase bans are: this rule exists to stop the MODEL
    asserting something about the sender that no resume backs. His own
    sentences about himself ARE the backing - checking them against the
    resume rejected "I'm Suryansh, studying Computer Science" for the crime
    of naming Suryansh.
    """
    known_text = " ".join(
        [_claim_field(c, "text") + " " + _claim_field(c, "source_span") for c in claims]
        + [str(t or "") for t in allowed_text]
        + [TEMPLATE_VOCABULARY]
    ).lower()
    known_tokens = set(_KNOWN_TOKEN.findall(known_text)) | _CLAIM_STOPWORDS
    known_numbers = _numbers_in(known_text)

    body_to_check = _generated_region(draft.body, company_name)
    hook_sentences = set(_sentences(_hook(draft.body)))

    errs: list[str] = []
    for sentence in _sentences(body_to_check):
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

    errs += _unsourced_work_stories(draft, claims)
    return errs


# Live finding (2026-09-17): the connection template asks for "a time I hit
# this problem myself", and with only a handful of real facts the model
# invented one - "wiring together a small retrieval service two weekends
# ago, when a mismatched payload schema sent the same request through
# twice". Nothing in it is a proper noun or a number, so the checks above
# saw nothing to match. A sentence claiming the sender DID something must
# name something real: a project, a role or an institution from the claims.
# The verb must belong to the SENDER: "I built ...", "I've spent ...".
# An earlier version matched the verb anywhere in the sentence and flagged
# "That reconciliation problem is what I keep thinking about" - a sentence
# about THEIR problem, which is exactly what part 1 is supposed to contain.
_WORK_VERBS = re.compile(
    r"\b(?:I|I've|I'd|my)\b[^.?!]{0,40}?\b(?:"
    r"built|building|wrote|writing|shipped|shipping|spent|spending|"
    r"wired|wiring|designed|designing|implemented|implementing|hacked|"
    r"debugged|debugging|untangled|reconciled|automated|"
    r"worked on|working on|put together|putting together|set up|setting up|"
    r"ran into|run into|hit that|felt that|dealt with)\b",
    re.IGNORECASE,
)
_MIN_CLAIM_NAME_LEN = 4


def _claim_names(claims) -> set[str]:
    """Distinctive words naming a thing the sender actually did - the
    project, employer and institution names in the claims."""
    names: set[str] = set()
    for c in claims:
        # Every kind counts, not just project/role/education: an operator
        # who edits a draft to cite a *skill* claim ("I wrote the ingest
        # layer in Rust", kept with --keep-unverified) is naming something
        # from the resume, and this rule must not override that choice.
        text = _claim_field(c, "text") + " " + _claim_field(c, "source_span")
        for word in re.findall(r"[A-Za-z][A-Za-z0-9.+#-]{2,}", text):
            if len(word) >= _MIN_CLAIM_NAME_LEN:
                names.add(word.lower().strip("."))
    return names


def _unsourced_work_stories(draft, claims) -> list[str]:
    names = _claim_names(claims)
    if not names:
        return []
    errs: list[str] = []
    for sentence in _sentences(draft.body):
        if not (_FIRST_PERSON.search(sentence) and _WORK_VERBS.search(sentence)):
            continue
        words = {w.lower().strip(".,;:!?()[]{}\"'") for w in sentence.split()}
        if not (words & names):
            errs.append(
                "tells a story about the sender that names nothing from the "
                f"resume: {sentence[:60]!r}"
            )
    return errs


def validate_draft(draft, source, company_name, one_liner, claims,
                   previous_body=None, extra_allowed_text=(), all_publications=(),
                   *, operator_edit: bool = False) -> list[str]:
    allowed = [company_name or "", one_liner or "", source.text if source else "",
               *extra_allowed_text]
    return (validate_style(draft, previous_body, company_name=company_name or "",
                           operator_edit=operator_edit)
            + validate_grounding(draft, source, company_name, one_liner, claims,
                                 all_publications=all_publications)
            + validate_claims(draft, claims, allowed_text=allowed,
                              company_name=company_name or ""))
