"""Task 15, ruling R2: the wire text of an outreach email is composed at
send time from the stored body.

The drafter writes — and the validator caps (D28) — only the *body*: no
greeting, no sign-off (Tasks 13/14 report, "disclosed guesses"). What
actually leaves the mailbox is

    Hi {first name},

    {body}

    {signature}

    {opt-out line, if there is one}

`compose_wire_text` is the single pure function that produces it, so the
sender (Task 15) and the review screen's preview (Task 16) cannot drift
apart: what the human approves is byte-for-byte what is sent. The stored
body is never modified.

**The opt-out line is empty (operator instruction, 2026-09-23: "delete
this line from the email").** Spec §18 asks for "a single-line opt-out
sentence in every email"; the person whose name is on the letter has
decided against one, and his letter already ends by telling the founder
a "no" needs no explanation. That is a deliberate departure from the
spec, recorded here rather than quietly dropped.

Emptying the constant, rather than deleting the part, is what makes this
reversible: `compose_wire_text` drops empty parts, so the wire text is
already correct, and putting a sentence back here is the whole change.
It also takes effect on letters already drafted - this is composed at
send and preview time, never stored in a body.
"""

import re
from html import escape

from app.domain.rich_text import sanitize, to_text

OPT_OUT_LINE = ""

_FALLBACK_GREETING_NAME = "there"

# A first line that is itself a greeting: "Hi Deepti," / "Hey," / "Dear
# Ms. Rao," and nothing else on that line. Live finding (2026-09-23): the
# operator pasted a complete letter, greeting included, into one draft by
# hand, and the Approved screen showed "Hi Deepti," twice - once from his
# own text and once from this module. The stored body is the operator's
# and is never modified (ruling R2), so the fix belongs here: when the
# letter already opens with a greeting, do not add a second one.
_GREETING_LINE = re.compile(r"^\s*(?:hi|hey|hello|dear)\b([^,\n]*),\s*$", re.IGNORECASE)


def first_name(full_name: str | None) -> str:
    """First whitespace-separated token of the founder's full name that is
    not an honorific (a token ending in ".", e.g. "Dr.", "Mr.", "Ms." — fix
    round 1, F9), or "there" (as in "Hi there,") when nothing usable is
    left — a blank greeting ("Hi ,") is worse than a generic one."""
    tokens = [t for t in (full_name or "").split() if not t.endswith(".")]
    return tokens[0] if tokens else _FALLBACK_GREETING_NAME


def greets(body: str, name: str) -> bool:
    """Does `body` already open with a greeting to `name`?

    The name has to match, or be absent as in "Hello," and "Hi there,". A
    greeting naming somebody ELSE is deliberately NOT recognised: that is
    a letter pasted from another founder's draft, and the doubled
    greeting it produces is exactly how the operator catches it on the
    review screen. Silently accepting it would send Deepti a letter
    opening "Hi Rahul,".
    """
    first_line = (body or "").lstrip().split("\n", 1)[0]
    match = _GREETING_LINE.match(first_line)
    if match is None:
        return False
    greeted = match.group(1).strip().casefold()
    return greeted in ("", _FALLBACK_GREETING_NAME, (name or "").strip().casefold())


def compose_wire_text(
    first_name: str, body: str, signature: str, opt_out_line: str = OPT_OUT_LINE
) -> str:
    """`Hi {first_name},\\n\\n{body}\\n\\n{signature}\\n\\n{opt_out_line}`.

    Each part is stripped of surrounding whitespace (interior untouched)
    and only non-empty parts are joined, so neither a stored body ending
    in a newline nor an empty signature (fix round 1, F2 — Task 16's
    preview may call this before a signature is configured) can produce a
    triple blank line.

    The greeting is skipped when the body already carries one to the same
    person (`greets`), so a letter written by hand, greeting and all,
    goes out reading the way it was written.
    """
    greeting = "" if greets(body, first_name) else f"Hi {first_name},"
    parts = (greeting, body.strip(), signature.strip(), opt_out_line.strip())
    return "\n\n".join(p for p in parts if p)


def compose_wire_html(
    first_name: str, body_html: str, signature: str, opt_out_line: str = OPT_OUT_LINE
) -> str:
    """The HTML part of a formatted letter: the same greeting, signature
    and opt-out as `compose_wire_text`, around the sanitised body.

    Built from the same rules as the plain text, on purpose - the two
    parts of one email must say the same thing, and a greeting added to
    one and not the other would be exactly the kind of drift R2 exists to
    prevent. Only ever called for a letter that HAS formatting; the rest
    are sent as plain text alone, as before.
    """
    body = sanitize(body_html)

    def paragraph(text: str) -> str:
        lines = [escape(line, quote=False) for line in text.strip().split("\n")]
        return "<p>" + "<br>".join(lines) + "</p>"

    parts = []
    if not greets(to_text(body), first_name):
        parts.append(paragraph(f"Hi {first_name},"))
    parts.append(body)
    for extra in (signature, opt_out_line):
        if extra.strip():
            parts.append(paragraph(extra))
    return "".join(parts)
