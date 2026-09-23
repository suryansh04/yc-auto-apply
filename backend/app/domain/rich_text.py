"""Formatted letters: bold, italic, underline, text size and lists.

Operator request (2026-09-23): "give option such as bolding, italic,
increasing size of a particular text, numerical or bullet points and so
on so that i can really customize it".

Until then the letter was plain text by rule (D10), and for a good
reason: it was SENT as plain text, so a bold marker reached the founder
as a literal asterisk. Formatting therefore means an HTML email, with the
plain text still sent beside it for clients that do not render HTML.

**The plain text stays canonical.** Everything that reads a letter - the
checks in `validation`, the anchors `letter.insight_region` finds the
company paragraph by, the plain-text part of the email - keeps reading
`body`. The formatted version lives beside it in `body_html`, and `body`
is always DERIVED from it here (`to_text`), never sent separately by a
client, so the two cannot disagree.

**It is an allowlist.** Whatever the editor sends is re-serialised from
scratch: only the tags below survive, the one attribute that does is a
`font-size` from a fixed set, and every character of text is escaped.
What comes out is safe to put in an email and to render in the app.

Pure: the standard library's `html.parser` and nothing else.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

# Tag -> the tag it is written as. `b` and `i` are what a paste from
# another editor produces; they mean the same thing.
_INLINE = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "u": "u"}
_BLOCK = {"p", "ul", "ol"}
# Contents are dropped along with the tag, not kept as text.
_DROPPED = {"script", "style", "head", "title"}

# The sizes the editor offers. Anything else a paste carries in is
# dropped rather than trusted: a 72px line in a cold email is a mistake,
# and this is the one place a mistake like that can be stopped.
FONT_SIZES = ("13px", "16px", "20px", "24px")


def _font_size(style: str | None) -> str | None:
    for part in (style or "").split(";"):
        name, _, value = part.partition(":")
        if name.strip().lower() == "font-size" and value.strip().lower() in FONT_SIZES:
            return value.strip().lower()
    return None


class _Node:
    __slots__ = ("tag", "size", "children")

    def __init__(self, tag: str, size: str | None = None):
        self.tag = tag
        self.size = size
        self.children: list = []


class _TreeBuilder(HTMLParser):
    """HTML in, a tree of ONLY the allowed nodes out.

    Unknown tags are unwrapped (their text kept); `_DROPPED` tags lose
    their text too. Blocks that turn up inside inline markup, or text
    outside any block, are tolerated - the serialiser below makes
    paragraphs of whatever it is given.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._stack = [self.root]
        self._dropping = 0

    def _open(self, node: _Node) -> None:
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROPPED:
            self._dropping += 1
            return
        if self._dropping:
            return
        if tag == "br":
            self._stack[-1].children.append(_Node("br"))
        elif tag in _BLOCK:
            # A block never nests inside a paragraph; close back out to
            # the nearest container that can hold it.
            while self._stack[-1].tag not in ("root", "li"):
                self._stack.pop()
            self._open(_Node(tag))
        elif tag == "li":
            while self._stack[-1].tag not in ("root", "ul", "ol"):
                self._stack.pop()
            if self._stack[-1].tag == "root":
                self._open(_Node("ul"))
            self._open(_Node("li"))
        elif tag in _INLINE:
            self._open(_Node(_INLINE[tag]))
        elif tag == "span":
            self._open(_Node("span", _font_size(dict(attrs).get("style"))))

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br" and not self._dropping:
            self._stack[-1].children.append(_Node("br"))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROPPED:
            self._dropping = max(0, self._dropping - 1)
            return
        if self._dropping:
            return
        wanted = _INLINE.get(tag, tag)
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == wanted:
                del self._stack[depth:]
                return

    def handle_data(self, data):
        if not self._dropping and data:
            self._stack[-1].children.append(data)


def _parse(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return builder.root


# --- HTML out -----------------------------------------------------------


def _inline_html(children) -> str:
    out = []
    for child in children:
        if isinstance(child, str):
            out.append(escape(child, quote=False))
        elif child.tag == "br":
            out.append("<br>")
        elif child.tag == "span":
            inner = _inline_html(child.children)
            out.append(f'<span style="font-size: {child.size}">{inner}</span>' if child.size else inner)
        elif child.tag in ("strong", "em", "u"):
            inner = _inline_html(child.children)
            if inner:
                out.append(f"<{child.tag}>{inner}</{child.tag}>")
        else:  # a stray block inside inline markup: keep its words
            out.append(_inline_html(child.children))
    return "".join(out)


def _list_html(node: _Node) -> str:
    items = [c for c in node.children if isinstance(c, _Node) and c.tag == "li"]
    rendered = [f"<li>{_inline_html(li.children).strip()}</li>" for li in items]
    rendered = [r for r in rendered if r != "<li></li>"]
    return f"<{node.tag}>{''.join(rendered)}</{node.tag}>" if rendered else ""


def _blocks_of(root: _Node) -> list[str]:
    """Every top-level block, as sanitised HTML. Loose inline content
    between blocks becomes a paragraph of its own."""
    blocks, loose = [], []

    def flush():
        inner = _inline_html(loose).strip()
        # A run of loose text is a paragraph; `<br>`-only runs are not.
        if inner and inner.replace("<br>", "").strip():
            blocks.append(f"<p>{inner}</p>")
        loose.clear()

    for child in root.children:
        if isinstance(child, _Node) and child.tag in _BLOCK:
            flush()
            if child.tag == "p":
                inner = _inline_html(child.children).strip()
                if inner and inner.replace("<br>", "").strip():
                    blocks.append(f"<p>{inner}</p>")
            else:
                html = _list_html(child)
                if html:
                    blocks.append(html)
        elif isinstance(child, str) and not child.strip() and not loose:
            continue  # whitespace between blocks
        else:
            loose.append(child)
    flush()
    return blocks


def sanitize(html: str) -> str:
    """Only the allowed markup survives; every character of text is
    escaped. Idempotent: `sanitize(sanitize(x)) == sanitize(x)`."""
    return "".join(_blocks_of(_parse(html)))


# --- text out -----------------------------------------------------------


def _inline_text(children) -> str:
    out = []
    for child in children:
        if isinstance(child, str):
            out.append(child)
        elif child.tag == "br":
            out.append("\n")
        else:
            out.append(_inline_text(child.children))
    return "".join(out)


def _block_text(block_html: str) -> str:
    node = _parse(block_html).children[0]
    if node.tag == "p":
        return _inline_text(node.children).strip()
    items = [c for c in node.children if isinstance(c, _Node) and c.tag == "li"]
    lines = []
    for number, li in enumerate(items, start=1):
        marker = f"{number}. " if node.tag == "ol" else "• "
        lines.append(marker + _inline_text(li.children).strip())
    return "\n".join(lines)


def to_text(html: str) -> str:
    """The letter as plain text: paragraphs separated by a blank line,
    `<br>` as a line break, bullets as "• ", numbered items as "1. ".

    The bullets match the letter's own plain-text bullets
    (`letter.BUILT`), so turning that block into a real list in the
    editor leaves the anchors that find the company paragraph intact."""
    return "\n\n".join(_block_text(b) for b in _blocks_of(_parse(html)))


# --- plain text in ------------------------------------------------------


def _paragraph_html(text: str) -> str:
    return "<p>" + "<br>".join(escape(line, quote=False) for line in text.split("\n")) + "</p>"


def from_text(text: str) -> str:
    """Plain text as the HTML the editor starts from: one `<p>` per
    paragraph, line breaks kept. Bullet-looking lines stay text - turning
    them into a real list is the operator's call, in the editor."""
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    return "".join(_paragraph_html(p) for p in paragraphs)


def has_formatting(html: str) -> bool:
    """Does this HTML say anything its plain text does not?

    A letter nobody formatted is stored and sent as plain text, exactly
    as before - an HTML part that adds nothing is one more thing for a
    spam filter to look at."""
    clean = sanitize(html)
    return bool(clean) and clean != from_text(to_text(clean))


def carry_formatting(old_html: str | None, new_text: str) -> str | None:
    """`new_text` as HTML, keeping the formatting of every paragraph that
    did not change.

    Why this exists: swapping in the AI paragraph or the generic line
    changes ONE paragraph of a letter by plain text (`letter.with_insight`).
    Without this, doing that to a letter the operator had formatted would
    quietly throw all his formatting away. Each paragraph of the new text
    that is word-for-word a paragraph of the old one gets its old HTML
    back; anything new or changed arrives unformatted.

    None when there was no formatting to carry, or none survives."""
    if not old_html:
        return None
    by_text = {}
    for block in _blocks_of(_parse(old_html)):
        by_text.setdefault(_block_text(block), block)
    paragraphs = [p.strip() for p in (new_text or "").split("\n\n") if p.strip()]
    html = "".join(by_text.get(p) or _paragraph_html(p) for p in paragraphs)
    return html if has_formatting(html) else None
