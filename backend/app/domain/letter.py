"""The operator's letter, assembled rather than generated.

Operator decision (2026-09-22), given as a finished example email to
Zave: most of a cold email from this sender is the same every time. Who
he is, where he interned, what he built and what he is asking for do not
change between one founder and the next, and they are all facts about
HIM. The model has no business rewriting them - three live drafts were
rejected for exactly that (a metric from the Better internship attributed
to WaveWatch, a fabricated "I signed up and tried it", a tone example's
"your planner" copied into a procurement company's email).

So the model writes ONE thing: the paragraph about the company, grounded
in the research claims. Everything else below is the operator's own
wording, pasted in unchanged. A fact about the sender can no longer be
garbled, because nothing regenerates it.

Edit the blocks here and nowhere else - this module is the copy.

The assembled body is still what `compose_wire_text` wraps: the greeting,
the signature and the opt-out line are added at send time, exactly as
before.
"""

# --- the operator's fixed blocks, in order -----------------------------

# Disarms the cold-email frame before the founder has decided to stop
# reading. "2.5 mins" is a promise the length has to keep, which is why
# MAX_WORDS in validation.py is a real cap and not decoration.
OPENER = (
    "This'll take 2.5 mins and I promise it's not another Bullshit AI "
    "Generated cold mail."
)

INTRO = (
    "I'm Suryansh, studying Computer Science. I build things compulsively, "
    "mostly because I can't sit still when I see a problem worth solving."
)

BACKGROUND = (
    "Recently, I spent time at Better building tech for early-stage startups "
    "going from 0 to 1. That's what got me hooked on startups. Once you've "
    "watched something go from an empty repo to people actually using it, "
    "working anywhere else feels slow."
)

BUILT = (
    "Some of what I've built: WaveWatch, a coastal risk monitoring system "
    "that won Smart India Hackathon 2025 out of 68,000+ teams. Lerno.ai, "
    "which turns any topic into animated video lessons and won Codeclash 2.0 "
    "at the Google office out of 1,000+ teams. App Dev, AI agents, frontend, "
    "and backend to own the whole thing end to end."
)

# The hinge. Everything above is about the sender; everything below is
# about the founder. Saying so outright is what earns the second half.
SECTION_BREAK = "Now Here's the Important Part:"

ASK = (
    "I can see that you have great users and growth, these users come with "
    "issues they face. I'd like to be one of the people fixing it as an intern."
)

# `{company}` is the only slot: the display name, so the last line names
# the company the founder actually runs.
CLOSE = (
    "I have attached my resume. Would love to hop a 15 min call, whenever "
    "works to understand your motivation behind building {company}!"
)

# --- the one paragraph the sender must write himself -------------------

# Operator decision (2026-09-22): the "I'm exactly who you're building
# for" paragraph is the strongest beat in the example email AND the one
# thing a model cannot know. It rests on having actually used the product
# ("I tried your app earlier when the game was live"), which is true of
# Zave and false of a robot-arm company - and the founder can check their
# own signup list.
#
# So the model never writes it. Where the company is something this
# sender could plausibly be a user of, the draft carries this marker
# instead, and the operator either types the real thing at review time or
# deletes the block. `contains_stub` is what stops a marker reaching a
# founder: `send` refuses a body that still has one.
USER_FIT_MARKER = ">>> YOUR TURN"

USER_FIT_STUB = (
    f"{USER_FIT_MARKER}: are you actually their user? Say the real, specific "
    "thing here - what you use it for, what you noticed while using it. "
    "Delete this whole paragraph if you are not."
)


def contains_stub(body: str) -> bool:
    """True while the operator's own paragraph is still a placeholder.

    Checked at send time rather than at draft time: an unfilled stub is
    not a bad draft to be retried, it is a draft waiting on the one person
    who knows the answer.
    """
    return USER_FIT_MARKER in (body or "")


def fixed_blocks(company_name: str) -> list[str]:
    """Every paragraph the model does not write, in order, with the
    company's display name filled into the close."""
    return [
        OPENER,
        INTRO,
        BACKGROUND,
        BUILT,
        SECTION_BREAK,
        ASK,
        CLOSE.format(company=company_name),
    ]


def assemble_body(
    company_name: str,
    insight_paragraphs: list[str],
    *,
    include_user_fit_stub: bool = False,
) -> str:
    """The stored body: fixed blocks with the generated company paragraphs
    dropped into the one place they belong, after the section break.

    `insight_paragraphs` is what the model returned. Empty entries are
    dropped rather than joined into a double blank line, so a model that
    pads its array cannot change the letter's shape.
    """
    insight = [p.strip() for p in insight_paragraphs if p and p.strip()]
    parts = [OPENER, INTRO, BACKGROUND, BUILT, SECTION_BREAK, *insight]
    if include_user_fit_stub:
        parts.append(USER_FIT_STUB)
    parts += [ASK, CLOSE.format(company=company_name)]
    return "\n\n".join(parts)


def fixed_word_count(company_name: str = "Company") -> int:
    """How many of the body's words are the operator's own, before the
    model writes anything. The word cap in validation.py is set from this
    plus room for the company paragraphs - so a cap that no longer fits
    the fixed blocks is a test failure, not a mystery rejection later."""
    return sum(len(block.split()) for block in fixed_blocks(company_name))
