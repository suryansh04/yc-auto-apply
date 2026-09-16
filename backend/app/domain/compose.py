"""Task 15, ruling R2: the wire text of an outreach email is composed at
send time from the stored body.

The drafter writes — and the validator caps (D28) — only the *body*: no
greeting, no sign-off (Tasks 13/14 report, "disclosed guesses"). What
actually leaves the mailbox is

    Hi {first name},

    {body}

    {signature}

    {opt-out line}

`compose_wire_text` is the single pure function that produces it, so the
sender (Task 15) and the review screen's preview (Task 16) cannot drift
apart: what the human approves is byte-for-byte what is sent. The stored
body is never modified.

Spec §18 requires "a single-line opt-out sentence in every email" but does
not fix its wording, so `OPT_OUT_LINE` below is this module's wording —
one line, plain text (D10), offering a reply-based way out that costs the
founder one word. Change it here and nowhere else.
"""

OPT_OUT_LINE = "If not, a one-word \"no\" is fine and I won't follow up."

_FALLBACK_GREETING_NAME = "there"


def first_name(full_name: str | None) -> str:
    """First whitespace-separated token of the founder's full name that is
    not an honorific (a token ending in ".", e.g. "Dr.", "Mr.", "Ms." — fix
    round 1, F9), or "there" (as in "Hi there,") when nothing usable is
    left — a blank greeting ("Hi ,") is worse than a generic one."""
    tokens = [t for t in (full_name or "").split() if not t.endswith(".")]
    return tokens[0] if tokens else _FALLBACK_GREETING_NAME


def compose_wire_text(
    first_name: str, body: str, signature: str, opt_out_line: str = OPT_OUT_LINE
) -> str:
    """`Hi {first_name},\\n\\n{body}\\n\\n{signature}\\n\\n{opt_out_line}`.

    Each part is stripped of surrounding whitespace (interior untouched)
    and only non-empty parts are joined, so neither a stored body ending
    in a newline nor an empty signature (fix round 1, F2 — Task 16's
    preview may call this before a signature is configured) can produce a
    triple blank line.
    """
    parts = (f"Hi {first_name},", body.strip(), signature.strip(), opt_out_line.strip())
    return "\n\n".join(p for p in parts if p)
