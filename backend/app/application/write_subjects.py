"""Subject lines, written for each company.

Operator request (2026-09-23): "a agent specifically for writing random
catchy punchy subject instead of generic one which we are currently use
'Would love to build at GroMo'".

That fixed subject was his own line, chosen earlier the same day because
a model rewriting it every morning could only get it wrong. It is still
the answer whenever this cannot produce something usable - a missing key,
a failed call, three candidates that all break a rule. The letter always
has a subject; the model only ever gets to improve on it.

Pure application logic - depends on the `SubjectWriter` port, never on a
concrete adapter.

**Advice never fails loudly**, the rule `suggest_fixes` and
`write_insight` follow: `run_subject_writer` never raises, and logs a
failure by exception type name only.

A candidate is dropped when it:

- is not 3 to 10 words - the same bounds the letter's own checks put on
  a subject (`validation.MIN/MAX_SUBJECT_WORDS`), so a subject offered
  here is never one the checks then complain about;
- pretends to be a reply or a forward ("Re:", "Fwd:") - he has never
  written to this founder, and pretending otherwise is a lie;
- is one of the subjects every founder deletes ("Internship inquiry",
  "Quick question") - the reason this exists at all;
- carries an exclamation mark, a question mark, emoji, a shouted word, a
  number, or a phrase the letter bans everywhere else;
- repeats one already offered.
"""

from __future__ import annotations

import logging
import re

from app.domain.names import company_name_variants
from app.domain.validation import (
    BANNED_PHRASES,
    MAX_SUBJECT_WORDS,
    MIN_SUBJECT_WORDS,
    TRIED_IT_PHRASES,
)

log = logging.getLogger(__name__)

# How many are offered on the review screen. Three styles are asked for
# (their choice / the ask / a hook); more than that is a list to read,
# not a choice to make.
OFFERED = 3

_FAKE_THREAD = re.compile(r"^\s*(re|fwd?|fw)\s*:", re.IGNORECASE)
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_DIGIT = re.compile(r"\d")

# The subjects every founder deletes unread. Substring matches, so
# "internship inquiry for GroMo" is caught with the rest.
_GENERIC = (
    "internship inquiry", "internship enquiry", "internship application",
    "internship opportunity", "quick question", "opportunity", "collaboration",
    "introduction", "following up", "checking in", "reaching out", "hello",
)
_HYPE = (
    "amazing", "incredible", "game-changing", "game changing", "revolutionary",
    "disrupt", "passionate", "excited", "you won't believe", "urgent", "free",
    "guaranteed", "act now", "limited",
)
_TELLS = tuple(p.casefold() for p in (*BANNED_PHRASES, *TRIED_IT_PHRASES, *_GENERIC, *_HYPE))


def subject_problem(subject: str, company_name: str = "") -> str | None:
    """Why this subject cannot be offered, or None when it can."""
    text = (subject or "").strip()
    if not text:
        return "empty"
    words = text.split()
    if not MIN_SUBJECT_WORDS <= len(words) <= MAX_SUBJECT_WORDS:
        return f"{len(words)} words"
    if _FAKE_THREAD.match(text):
        return "pretends to be a reply or a forward"
    if "!" in text or "?" in text:
        return "exclamation or question mark"
    if _EMOJI.search(text):
        return "emoji"
    # Digits and shouting are allowed only where they are the company's
    # own name (a "Y42", an "HDFC").
    name_words = {
        w.casefold() for v in (company_name_variants(company_name) if company_name else [])
        for w in v.split()
    }
    others = [w.strip(".,:;'\"") for w in words if w.strip(".,:;'\"").casefold() not in name_words]
    if any(_DIGIT.search(w) for w in others):
        return "a number"
    # An acronym is not shouting: his own example is "I'd love to build
    # Kodo's MCP". Up to four capitals is an acronym (AI, MCP, API, SAAS),
    # and one is allowed; a longer capitalised word, or two acronyms, is.
    capitals = [w.removesuffix("'s") for w in others if w.isupper() and len(w) >= 2]
    if any(len(w) > 4 for w in capitals) or len(capitals) > 1:
        return "a shouted word"
    folded = text.casefold()
    # Operator request (2026-09-24), with five examples all for Kodo:
    # "Would love to build at Kodo", "I'd love to build Kodo's MCP", ... -
    # "not completely similar but the intent should be the same". The
    # intent is two things every one of them has: the company's name, and
    # wanting to BUILD there. A clever hook without them is not his subject.
    names = [v.casefold() for v in (company_name_variants(company_name) if company_name else [])]
    if names and not any(n in folded for n in names):
        return "does not name the company"
    if not re.search(r"\bbuild", folded):
        return "is not about building there"
    for phrase in _TELLS:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", folded):
            return f"uses {phrase!r}"
    return None


def run_subject_writer(
    writer,
    company_name: str,
    one_liner: str = "",
    description: str = "",
    paragraph: str = "",
    avoid: list[str] | None = None,
) -> list[str]:
    """Up to `OFFERED` usable subjects, best first; `[]` when there are
    none. Never raises (see module docstring)."""
    seen = {a.strip().casefold() for a in (avoid or []) if a and a.strip()}
    try:
        candidates = writer.write({
            "company_name": company_name,
            "one_liner": one_liner or "",
            "description": description or "",
            "paragraph": paragraph or "",
            "avoid": [a for a in (avoid or []) if a and a.strip()],
        })
    except Exception as exc:  # noqa: BLE001 - a provider failure is advice that did not arrive
        log.warning("subjects: writer failed (%s)", type(exc).__name__)
        return []

    usable = []
    for raw in candidates or []:
        subject = " ".join(str(raw).split()).strip().strip('"').strip()
        # Live finding (2026-09-23): asked for all-lowercase subjects, the
        # model wrote "groMo". A founder notices their own name spelled
        # wrong before anything else, so it is put back exactly as given.
        if company_name:
            subject = re.sub(re.escape(company_name), company_name, subject, flags=re.IGNORECASE)
        problem = subject_problem(subject, company_name)
        if problem is None and subject.casefold() in seen:
            problem = "already offered"
        if problem is not None:
            log.info("subjects: dropped %r for %s - %s", subject, company_name, problem)
            continue
        seen.add(subject.casefold())
        usable.append(subject)
        if len(usable) == OFFERED:
            break
    return usable
