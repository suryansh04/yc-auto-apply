# backend/app/domain/validation.py
"""Deterministic anti-slop rules. Spec §10. These are domain code, not prompt
instructions, precisely so they can be unit-tested and cannot be argued with."""
import re

MAX_WORDS = 120
MAX_SENTENCE_WORDS = 25
MAX_PARAGRAPHS = 3
MAX_SENTENCES_PER_PARAGRAPH = 3
MAX_EM_DASHES = 2

BANNED_PHRASES = [
    "i hope this email finds you well", "i am reaching out", "passionate about",
    "leverage", "synergy", "game-changing", "i came across your company",
    "delve", "tapestry", "in today's fast-paced", "reach out", "circle back",
    "at your earliest convenience", "i would love the opportunity",
    "please do not hesitate",
]
SENIORITY_PHRASES = [
    "years of experience", "in my current role", "my team", "i led a team",
    "throughout my career",
]
LOCATION_PHRASES = ["remote", "relocate", "based in", "work from"]
INTERN_TOKENS = ["internship", "intern", "interning"]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\S+", text) if w]


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

    paragraphs = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    if len(paragraphs) > MAX_PARAGRAPHS:
        errs.append(f"more than {MAX_PARAGRAPHS} paragraphs")
    for p in paragraphs:
        if len(_sentences(p)) > MAX_SENTENCES_PER_PARAGRAPH:
            errs.append("a paragraph exceeds 3 sentences")
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
        if phrase in low:
            errs.append(f"raises location: {phrase!r}")

    if not any(t in low for t in INTERN_TOKENS):
        errs.append("no explicit internship ask")

    if body.count("—") > MAX_EM_DASHES:
        errs.append("more than two em dashes")

    subject_words = _words(draft.subject)
    if not 3 <= len(subject_words) <= 6:
        errs.append("subject is not between three and six words")
    if any(w.isupper() and len(w) > 1 for w in subject_words):
        errs.append("subject contains an all-caps word")
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", draft.subject):
        errs.append("subject contains emoji")

    if draft.sequence_step > 0 and previous_body is not None:
        if len(_words(body)) >= len(_words(previous_body)):
            errs.append("follow-up is not shorter than the previous message")

    return errs
