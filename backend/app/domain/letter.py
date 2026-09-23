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

**Rewritten 2026-09-23** from a second finished example, to Mayank at
Loop Health. What changed, and why it matters to the rest of the code:

- There is no "Now Here's the Important Part:" hinge any more. The
  company paragraph sits between `BUILT` and the ask, and those two are
  the anchors everything else uses to find it (`insight_region`). The
  bridge line that used to close it went on the operator's say-so on
  2026-09-23; `OLD_BRIDGE` records it, because letters drafted before
  then still carry it.
- The subject line is written here too (`SUBJECT`), for the same reason
  every other block is: it is the same every time, so a model that
  rewrites it every morning can only get it wrong.
- `ASK` no longer says "I can see that you have great users and growth".
  That was the one fixed line that made a claim about the FOUNDER, and
  it was being sent to pre-launch companies.
- The letter is plain text (D10). The example used markdown bold; bold
  markers would reach the founder as literal asterisks, so the emphasis
  is dropped and the bullet list kept.

The assembled body is still what `compose_wire_text` wraps: the greeting
and the signature are added at send time (there is no opt-out line as of
2026-09-23 - see `app.domain.compose`).
"""

# --- the operator's fixed blocks, in order -----------------------------

# Disarms the cold-email frame before the founder has decided to stop
# reading. "45 seconds" is a promise the length has to keep, which is why
# MAX_WORDS in validation.py is a real cap and not decoration.
#
# Operator request (2026-09-23): the "I promise it's not another
# AI-generated cold mail" half is gone. The old wording is kept below
# (`OLD_OPENER`) so letters drafted before today can be recognised and
# moved onto this one (`scripts/rebuilt.py`).
OPENER = "This'll take 45 seconds."
OLD_OPENER = (
    "This'll take 45 seconds, and I promise it's not another AI-generated "
    "cold mail."
)

INTRO = (
    "I'm Suryansh, a Computer Science student who builds things "
    "compulsively. Mostly because I can't sit still when I see a problem "
    "that I think is worth solving."
)

BACKGROUND = (
    "I recently spent some time at Better, working on products for "
    "early-stage startups going from 0 to 1. That experience got me really "
    "interested in startups. There's something pretty addictive about "
    "seeing an empty repo turn into something people actually use. After "
    "that, working somewhere where you don't get to build feels a little "
    "slow."
)

# One block, blank line and bullets included, so it is a single exact
# string to strip, swap and count. Plain text throughout (D10): no bold
# markers, no smart quotes, nothing that renders as punctuation debris in
# a plain-text mail client.
#
# Operator request (2026-09-23): the list is now three numbered items, in
# his words, led by the Better work. `OLD_BUILT` is the list it replaced,
# kept because it is also the anchor that finds the company paragraph in
# every letter drafted before today.
BUILT = (
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
OLD_BUILT = (
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

# Operator request (2026-09-23): "That's the kind of problem I'd
# genuinely like to work on." is GONE. It was a line about the company
# paragraph rather than a line of its own, and the paragraph now ends on
# what the sender wants to build anyway - so it said the same thing
# twice, in the flatter of the two voices.
#
# It was also the letter's closing anchor: everything between `BUILT` and
# it was the company paragraph. `ASK` takes over that job below, which it
# can because its first words are the same in every letter (the company
# name only appears in its last sentence).
OLD_BRIDGE = "That's the kind of problem I'd genuinely like to work on."

# `{company}` is the display name. 2026-09-23: this block used to open
# "I can see that you have great users and growth" - a claim about the
# founder's company, in the one part of the letter nothing verifies, sent
# to pre-launch companies as readily as to anyone else. It says what the
# sender wants now, and asserts nothing about them.
ASK = (
    "I'm looking for an internship where I can build things, ship them, and "
    "actually take ownership instead of just sitting on the sidelines. I'd "
    "love to do that at {company}."
)

CLOSE = (
    "I've attached my resume. If you think there could be a fit, I'd love to "
    "get 15 minutes with you and learn more about what you're building."
)

# Everything up to the company name, which is what makes this usable as
# an anchor: `ASK` is the first fixed block after the company paragraph,
# and this prefix is identical in every letter.
ASK_ANCHOR = ASK.split("{company}")[0].rstrip()

# Operator request (2026-09-23): "change the subject to something like
# this 'Would love to build at Loop'". Written down here rather than
# asked of the model, for the reason every other block here is: it is the
# operator's own words, it is the same every time, and a model that
# writes it again each morning is a model that can get it wrong. Three to
# ten words either way (`validation.MIN/MAX_SUBJECT_WORDS`), so a company
# whose display name runs long still passes.
SUBJECT = "Would love to build at {company}"


def subject(company_name: str) -> str:
    """The subject line, naming the company and nothing else."""
    return SUBJECT.format(company=company_name)

# --- the one paragraph that is about THEM -------------------------------

# The model writes this, grounded in a real source. It is the only part
# of the letter that could be wrong about the founder, which is why it is
# the only part the grounding checks score (`validation._hook`).
#
# Operator request (2026-09-23): "give option for this to make a generic
# if i am not able to find good for this specifically line". Some
# companies have nothing public worth a specific sentence, and the
# alternative to a generic line is not a better specific line - it is a
# fabricated one, which is the failure this whole module exists to
# prevent. So there is a written generic, and swapping it in is one
# button (`ReviewOutreach.use_generic_line`).
#
# It claims NOTHING about the company - no users, no growth, no stage, no
# guess at what they are solving. Everything in it is a fact about the
# sender, which is the only kind of sentence that is safe to send to a
# company nobody has researched.
GENERIC_INSIGHT = (
    "I came across {company} while looking for teams working on problems "
    "worth the years it takes to solve them, and yours is one I kept "
    "reading about instead of scrolling past."
)


def generic_insight(company_name: str) -> str:
    """The fallback company paragraph, naming the company and nothing else."""
    return GENERIC_INSIGHT.format(company=company_name)


# --- the paragraph only the operator can write --------------------------

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
    company's display name filled into the ask."""
    return [
        OPENER,
        INTRO,
        BACKGROUND,
        BUILT,
        ASK.format(company=company_name),
        CLOSE,
    ]


def assemble_body(
    company_name: str,
    insight_paragraphs: list[str],
    *,
    include_user_fit_stub: bool = False,
) -> str:
    """The stored body: fixed blocks with the generated company paragraphs
    dropped into the one place they belong, between what he built and the
    bridge into the ask.

    `insight_paragraphs` is what the model returned. Empty entries are
    dropped rather than joined into a double blank line, so a model that
    pads its array cannot change the letter's shape.
    """
    insight = [p.strip() for p in insight_paragraphs if p and p.strip()]
    parts = [OPENER, INTRO, BACKGROUND, BUILT, *insight]
    if include_user_fit_stub:
        parts.append(USER_FIT_STUB)
    parts += [ASK.format(company=company_name), CLOSE]
    return "\n\n".join(parts)


def insight_region(body: str) -> str | None:
    """The company paragraph(s) as they currently stand in `body`, or None.

    Located by its anchors rather than by position: everything between the
    end of `BUILT` and the start of the ask. Returns None for a body that
    was not assembled from these blocks (a letter drafted before this
    module was rewritten, or one the operator restructured by hand) -
    guessing at which paragraph is "the company one" in a letter that does
    not have this shape would put a rewrite in the wrong place.

    `OLD_BRIDGE` and `OLD_BUILT` are still accepted as anchors
    (2026-09-23): letters drafted before those blocks changed still carry
    them, and they are reviewable letters, not history.
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
    """Whichever list of what he built opens the company region here."""
    for block in (BUILT, OLD_BUILT):
        if block in text:
            return block
    return None


def _tail_anchor(text: str) -> str | None:
    """Whichever block closes the company region in THIS letter."""
    if OLD_BRIDGE in text:
        return OLD_BRIDGE
    return ASK_ANCHOR if ASK_ANCHOR in text else None


def with_insight(body: str, paragraphs: list[str]) -> str | None:
    """`body` with its company paragraph(s) replaced, or None when the
    anchors are not both there.

    Only the region between the anchors changes, so an operator edit
    anywhere else in the letter survives the swap.
    """
    text = body or ""
    if insight_region(text) is None:
        return None
    built, anchor = _head_anchor(text), _tail_anchor(text)
    head = text.split(built, 1)[0] + built
    tail = anchor + text.split(built, 1)[1].split(anchor, 1)[1]
    fresh = [p.strip() for p in paragraphs if p and p.strip()]
    return "\n\n".join([head.rstrip(), *fresh, tail.lstrip()])


def fixed_word_count(company_name: str = "Company") -> int:
    """How many of the body's words are the operator's own, before the
    model writes anything. The word cap in validation.py is set from this
    plus room for the company paragraphs - so a cap that no longer fits
    the fixed blocks is a test failure, not a mystery rejection later."""
    return sum(len(block.split()) for block in fixed_blocks(company_name))
