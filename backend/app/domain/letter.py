"""The operator's letter, assembled rather than generated.

Operator decision (2026-09-22): most of a cold email from this sender is
the same every time. Who he is, where he interned, what he built and what
he is asking for do not change between one founder and the next, and
they are all facts about HIM. The model has no business rewriting them.

Edit the blocks here and nowhere else - this module is the copy.

**Rewritten 2026-09-25** from the operator's researched template, to
Harsh at WareIQ:

    I'm Suryansh, a final-year CS (Data Science) student at ...
    I just wrapped up an internship at Better, where I worked on Operate ...

    My main work there was the GitHub integration. I moved it ...
    [TIE-IN: While looking at WareIQ, I noticed you sync orders across
    50+ channels. Keeping that many integrations reliable is the kind of
    backend work I enjoyed most at Better.]

    [CURIOUS: The other thing I got curious about is RTO. How do you ...?
    I'd love to learn how your team approaches that.] [FOUNDER LINE]

    Outside work:

    Lerno.ai: ...
    WaveWatch: ...

    I'd love to intern at WareIQ this winter, remote or on-site. Resume
    attached.

What that means for the rest of the code:

- The model writes the company part: a tie-in that CONTINUES the Better
  paragraph (joined with a space, not a blank line) and the "curious"
  paragraph. `insight_region` finds it between `BETTER` and
  `OUTSIDE_ANCHOR`.
- The founder line is written by its own agent
  (`app.application.write_founder_line`) and sits at the end of the
  curious paragraph. It is stored on the message (`founder_line`) so a
  swap of the company part can keep it, and a swap of the line can find
  it.
- The previous letter's blocks are kept as `OLD_*` so letters drafted
  before today (already approved or sent) are still recognised: their
  company paragraph can still be found, and their fixed wording is still
  exempt from the checks that exist to police the MODEL.

The assembled body is what `compose_wire_text` wraps: the greeting and
the signature are added at send time (`app.domain.compose`).
"""

# --- the operator's fixed blocks, in order -----------------------------

INTRO = (
    "I'm Suryansh, a final-year CS (Data Science) student at Manipal "
    "University Jaipur. I just wrapped up an internship at Better, where I "
    "worked on Operate, an AI agent that debugs production incidents."
)

# The company tie-in continues THIS paragraph, so it is also the anchor
# that opens the company region.
BETTER = (
    "My main work there was the GitHub integration. I moved it to read-only "
    "access and cut repeated GitHub token requests by 98% with caching."
)

# Operator request (2026-09-25): "make the outside work little detailed
# with bullet points and proper formatting". Every detail is from his
# resume (Lerno.ai: full stack, ~8 min -> under 3 with async rendering;
# WaveWatch: the whole backend, 20+ REST APIs) - nothing here is new.
#
# One block, blank line included, so it is a single exact string to strip
# and count. The plain text uses "• " bullets, which is exactly what the
# formatted version (`OUTSIDE_HTML`) turns back into as plain text
# (`rich_text.to_text`), so the two never disagree.
OUTSIDE_ITEMS = [
    ("Lerno.ai", "An AI product that turns any topic into an animated video lesson. "
                 "I built the full stack and cut generation time from about 8 minutes "
                 "to under 3 with async rendering. Won CodeClash 2.0 against 1,000+ teams."),
    ("WaveWatch", "A coastal risk platform. I built the entire backend: 20+ REST APIs "
                  "for auth, incident reporting and admin triage. Won Smart India "
                  "Hackathon 2025 against 68,000+ teams."),
]
OUTSIDE = "Outside work:\n\n" + "\n".join(f"• {name}: {text}" for name, text in OUTSIDE_ITEMS)
OUTSIDE_ANCHOR = OUTSIDE.split("\n", 1)[0]
# The same block with proper formatting: a bold heading, a real list, the
# project names in bold. Sent as the HTML part of the email.
OUTSIDE_HTML = (
    "<p><strong>Outside work:</strong></p><ul>"
    + "".join(f"<li><strong>{name}</strong>: {text}</li>" for name, text in OUTSIDE_ITEMS)
    + "</ul>"
)
# The first version of today's block (morning of 2026-09-25), recognised
# so the drafts written with it can be moved onto the one above.
OLD_OUTSIDE = (
    "Outside work:\n"
    "\n"
    "Lerno.ai: turns any topic into an animated lesson. Won CodeClash 2.0 "
    "against 1,000+ teams.\n"
    "WaveWatch: coastal risk platform. Won Smart India Hackathon 2025 "
    "against 68,000+ teams."
)

# `{company}` is the display name. "this winter" is fixed on purpose
# (operator decision 2026-09-25) - change it here when the season does.
ASK = "I'd love to intern at {company} this winter, remote or on-site. Resume attached."

# Kept only as the pre-2026-09-25 subject fallback; the subject writer
# (`app.application.write_subjects`) normally replaces it.
SUBJECT = "Would love to build at {company}"


def subject(company_name: str) -> str:
    """The subject line, naming the company and nothing else."""
    return SUBJECT.format(company=company_name)


# --- the previous letter (2026-09-22 .. 2026-09-24), recognised only ----

OLD_OPENER = "This'll take 45 seconds."
OLDER_OPENER = (
    "This'll take 45 seconds, and I promise it's not another AI-generated "
    "cold mail."
)
OLD_INTRO = (
    "I'm Suryansh, a Computer Science student who builds things "
    "compulsively. Mostly because I can't sit still when I see a problem "
    "that I think is worth solving."
)
OLD_BACKGROUND = (
    "I recently spent some time at Better, working on products for "
    "early-stage startups going from 0 to 1. That experience got me really "
    "interested in startups. There's something pretty addictive about "
    "seeing an empty repo turn into something people actually use. After "
    "that, working somewhere where you don't get to build feels a little "
    "slow."
)
OLD_BUILT = (
    "A few things I've built:\n"
    "\n"
    "1. At Better: I shipped the GitHub integration for Operate, an AI agent "
    "that debugs production incidents. I moved it to safer read-only access "
    "and cut repeated token calls by 98% with caching.\n"
    "2. Lerno.ai: An AI learning platform that turns topics into animated "
    "lessons. Won CodeClash 2.0, competing against 1,000+ teams.\n"
    "3. WaveWatch: A coastal risk monitoring platform that won Smart India "
    "Hackathon 2025, competing against 68,000+ teams."
)
OLDER_BUILT = (
    "A few things I've built:\n"
    "\n"
    "• WaveWatch: A coastal risk monitoring platform that won Smart India "
    "Hackathon 2025, competing against 68,000+ teams.\n"
    "• Lerno.ai: An AI learning platform that turns topics into animated "
    "lessons and won CodeClash 2.0 at the Google office, competing against "
    "1,000+ teams.\n"
    "• I've worked across AI agents, frontend, backend, and app "
    "development, so I enjoy taking an idea from \"we should build this\" to "
    "actually making it work."
)
OLD_BRIDGE = "That's the kind of problem I'd genuinely like to work on."
OLD_ASK = (
    "I'm looking for an internship where I can build things, ship them, and "
    "actually take ownership instead of just sitting on the sidelines. I'd "
    "love to do that at {company}."
)
OLD_ASK_ANCHOR = OLD_ASK.split("{company}")[0].rstrip()
OLD_CLOSE = (
    "I've attached my resume. If you think there could be a fit, I'd love to "
    "get 15 minutes with you and learn more about what you're building."
)

# What he has done, as his letter states it - the only facts about him a
# model may lean on when it ties his work to theirs (2026-09-25).
ABOUT_HIM = "\n\n".join([INTRO, BETTER, OUTSIDE])


# --- the part that is about THEM ----------------------------------------

# Operator request (2026-09-23): a written generic for companies with
# nothing public worth a specific sentence. It claims NOTHING about the
# company - no users, no growth, no stage, no guess at what they solve.
# Two parts since 2026-09-25, like the model's: a tie-in that finishes the
# Better paragraph, and the curious paragraph.
GENERIC_INSIGHT = (
    "While looking at {company}, I kept wanting to know how it works under "
    "the hood, which is usually a good sign for me.\n\n"
    "I'd love to learn how your team decides what to build next, and where "
    "an intern could actually help."
)


def generic_insight(company_name: str) -> str:
    """The fallback company part, naming the company and nothing else."""
    return GENERIC_INSIGHT.format(company=company_name)


# --- the paragraph only the operator can write --------------------------

# Operator decision (2026-09-22): "are you actually their user?" is the
# one thing a model cannot know. Where the company is something this
# sender could plausibly be a user of, the draft carries this marker and
# the operator fills it in or deletes it. `send` refuses a body that
# still has one.
USER_FIT_MARKER = ">>> YOUR TURN"

USER_FIT_STUB = (
    f"{USER_FIT_MARKER}: are you actually their user? Say the real, specific "
    "thing here - what you use it for, what you noticed while using it. "
    "Delete this whole paragraph if you are not."
)


def contains_stub(body: str) -> bool:
    """True while the operator's own paragraph is still a placeholder."""
    return USER_FIT_MARKER in (body or "")


def fixed_blocks(company_name: str) -> list[str]:
    """Every paragraph of TODAY's letter the model does not write, in
    order, with the company's display name filled into the ask."""
    return [INTRO, BETTER, OUTSIDE, ASK.format(company=company_name)]


def known_blocks(company_name: str) -> list[str]:
    """Every block the operator has ever written, today's and the previous
    letter's. `validation` removes these before policing what the MODEL
    wrote, so a letter drafted before 2026-09-25 is not suddenly rejected
    for the operator's own words."""
    return [
        *fixed_blocks(company_name),
        OLD_OUTSIDE, OLDER_OPENER, OLD_OPENER, OLD_INTRO, OLD_BACKGROUND, OLD_BUILT,
        OLDER_BUILT, OLD_BRIDGE, OLD_ASK.format(company=company_name), OLD_CLOSE,
    ]


def assemble_body(
    company_name: str,
    insight_paragraphs: list[str],
    *,
    include_user_fit_stub: bool = False,
    founder_line: str | None = None,
    tie_in: str | None = None,
) -> str:
    """The stored body: fixed blocks with the model's company part dropped
    into place.

    `insight_paragraphs[0]` is the tie-in and CONTINUES the Better
    paragraph; the rest (normally one, the curious paragraph) follow as
    paragraphs of their own. `founder_line` ends the last of them. Empty
    entries are dropped, so a model that pads its array cannot change the
    letter's shape.

    `tie_in` (2026-09-25, his pipeline document): given - even empty -
    it is the ONLY text joined to the Better paragraph, and every entry
    of `insight_paragraphs` is a paragraph of its own. Most letters have
    no tie-in, and then the Better paragraph stands alone, as in his
    UrbanKisaan and Kosh examples.
    """
    insight = [p.strip() for p in insight_paragraphs if p and p.strip()]
    line = (founder_line or "").strip()
    if tie_in is not None:
        if line:
            insight = [*insight[:-1], f"{insight[-1]} {line}"] if insight else [line]
        tie = tie_in.strip()
        better = f"{BETTER} {tie}" if tie else BETTER
    else:
        if line:
            if len(insight) >= 2:
                insight[-1] = f"{insight[-1]} {line}"
            else:
                insight.append(line)
        better = BETTER
        if insight:
            better = f"{BETTER} {insight[0]}"
            insight = insight[1:]
    parts = [INTRO, better, *insight]
    if include_user_fit_stub:
        parts.append(USER_FIT_STUB)
    parts += [OUTSIDE, ASK.format(company=company_name)]
    return "\n\n".join(parts)


def formatted_html(body: str) -> str | None:
    """`body` as the formatted letter: every paragraph as it is, except
    "Outside work", which becomes `OUTSIDE_HTML`. None when the block is
    not in the body (edited by hand, or an older letter)."""
    from app.domain.rich_text import from_text

    text = body or ""
    if OUTSIDE not in text:
        return None
    before, after = text.split(OUTSIDE, 1)
    return from_text(before) + OUTSIDE_HTML + from_text(after)


def insight_region(body: str) -> str | None:
    """The company part as it currently stands in `body`, or None.

    Located by its anchors rather than by position: everything after
    `BETTER` up to "Outside work:" - or, in a letter drafted before
    2026-09-25, everything between the built list and the ask. None for a
    body that has neither shape: guessing which paragraph is the company
    one would put a rewrite in the wrong place.
    """
    text = body or ""
    head, tail = _head_anchor(text), _tail_anchor(text)
    if head is None or tail is None:
        return None
    after = text.split(head, 1)[1]
    if tail not in after:
        return None
    middle = after.split(tail, 1)[0]
    return middle.strip() or None


def _head_anchor(text: str) -> str | None:
    for block in (BETTER, OLD_BUILT, OLDER_BUILT):
        if block in text:
            return block
    return None


def _tail_anchor(text: str) -> str | None:
    if BETTER in text:
        return OUTSIDE_ANCHOR if OUTSIDE_ANCHOR in text else None
    if OLD_BRIDGE in text:
        return OLD_BRIDGE
    return OLD_ASK_ANCHOR if OLD_ASK_ANCHOR in text else None


def with_insight(body: str, paragraphs: list[str], *, tie_in: str | None = None) -> str | None:
    """`body` with its company part replaced, or None when the anchors are
    not both there. Only the region between the anchors changes, so an
    operator edit anywhere else survives the swap.

    In today's letter the first paragraph given continues the Better
    paragraph, exactly as `assemble_body` puts it.
    """
    text = body or ""
    if insight_region(text) is None:
        return None
    head_block, anchor = _head_anchor(text), _tail_anchor(text)
    head = text.split(head_block, 1)[0] + head_block
    tail = anchor + text.split(head_block, 1)[1].split(anchor, 1)[1]
    fresh = [p.strip() for p in paragraphs if p and p.strip()]
    if head_block == BETTER and tie_in is not None:
        # Same rule as `assemble_body`: only the tie-in joins Better.
        if tie_in.strip():
            head = f"{head.rstrip()} {tie_in.strip()}"
    elif head_block == BETTER and fresh:
        head = f"{head.rstrip()} {fresh[0]}"
        fresh = fresh[1:]
    return "\n\n".join([head.rstrip(), *fresh, tail.lstrip()])


def with_founder_line(body: str, old: str | None, new: str | None) -> str | None:
    """`body` with its founder line swapped for `new` (or removed, when
    `new` is empty), or None when there is nowhere to put it.

    `old` is the line the letter was last given. Found in the body, it is
    replaced where it stands - even if the operator moved it. Not found
    (never written, or edited by hand), `new` goes on the end of the
    company part, which is where `assemble_body` puts it.
    """
    text = body or ""
    new = (new or "").strip()
    old = (old or "").strip()
    if old and old in text:
        if new:
            return text.replace(old, new, 1)
        swapped = text.replace(" " + old, "", 1) if (" " + old) in text else text.replace(old, "", 1)
        return swapped
    if not new:
        return text
    region = insight_region(text)
    if region is None:
        return None
    # Never after his fill-it-in placeholder (live, Inito 2026-09-25): the
    # line ends the company paragraph, which comes before it.
    paragraph = region.split("\n\n" + USER_FIT_STUB, 1)[0].split(USER_FIT_STUB, 1)[0].rstrip()
    if not paragraph:
        return None
    return text.replace(paragraph, f"{paragraph} {new}", 1)


def fixed_word_count(company_name: str = "Company") -> int:
    """How many of the body's words are the operator's own, before the
    model writes anything. The word cap in validation.py is set from this
    plus room for the company part - so a cap that no longer fits the
    fixed blocks is a test failure, not a mystery rejection later."""
    return sum(len(block.split()) for block in fixed_blocks(company_name))
