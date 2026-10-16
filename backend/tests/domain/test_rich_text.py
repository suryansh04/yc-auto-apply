"""Formatted letters (`app.domain.rich_text`).

Operator request (2026-09-23): bold, italic, text size and lists. The
plain text stays canonical - checks, anchors and the plain-text part of
the email all read it - so these pin that the HTML and the text can never
disagree, and that nothing unsafe survives into an email.
"""
from html import escape

from app.domain import letter
from app.domain.rich_text import (
    carry_formatting,
    from_text,
    has_formatting,
    sanitize,
    to_text,
)


# --- what survives ------------------------------------------------------


def test_the_formatting_the_editor_offers_survives():
    html = ('<p><strong>bold</strong> <em>italic</em> <u>under</u> '
            '<span style="font-size: 20px">big</span></p>'
            '<ul><li>one</li><li>two</li></ul><ol><li>first</li></ol>')
    assert sanitize(html) == html


def test_b_and_i_from_a_paste_mean_strong_and_em():
    assert sanitize("<p><b>x</b> <i>y</i></p>") == "<p><strong>x</strong> <em>y</em></p>"


def test_anything_else_is_stripped_to_its_words():
    dirty = ('<p onclick="x()">Hi <a href="http://evil">there</a>'
             '<img src=x onerror=alert(1)></p><script>alert(1)</script>')
    clean = sanitize(dirty)
    assert clean == "<p>Hi there</p>"
    for bad in ("onclick", "href", "img", "script", "alert"):
        assert bad not in clean


def test_text_is_always_escaped():
    assert sanitize("<p>1 &lt; 2 &amp; &lt;b&gt;</p>") == "<p>1 &lt; 2 &amp; &lt;b&gt;</p>"


def test_only_the_offered_sizes_survive():
    # A 72px line in a cold email is a mistake, and this is where it stops.
    assert sanitize('<p><span style="font-size: 72px">huge</span></p>') == "<p>huge</p>"
    assert sanitize('<p><span style="color: red">red</span></p>') == "<p>red</p>"


def test_sanitize_is_idempotent():
    html = '<p><strong>a</strong><br>b</p><ul><li>c</li></ul>'
    assert sanitize(sanitize(html)) == sanitize(html)


def test_empty_paragraphs_and_items_disappear():
    assert sanitize("<p></p><p><br></p><ul><li></li></ul><p>kept</p>") == "<p>kept</p>"


# --- the plain text -----------------------------------------------------


def test_the_plain_text_is_paragraphs_lines_and_list_markers():
    html = "<p>One<br>line two</p><ul><li>a</li><li>b</li></ul><ol><li>x</li><li>y</li></ol>"
    assert to_text(html) == "One\nline two\n\n• a\n• b\n\n1. x\n2. y"


def test_a_real_list_of_what_he_built_keeps_the_letters_anchors():
    # Turning his "Outside work" lines into a real list in the editor
    # must not lose the company part: it is found by "Outside work:",
    # which the list comes after.
    body = letter.assemble_body("Zave", ["You built a layer on top of shopping."])
    html = from_text(body)
    lines = letter.OUTSIDE.split("\n\n")[1]
    listed = html.replace(
        from_text(lines),
        "<ul>" + "".join(f"<li>{escape(i, quote=False)}</li>" for i in lines.split("\n")) + "</ul>",
    )
    assert "<ul>" in listed
    assert letter.insight_region(to_text(listed)) == "You built a layer on top of shopping."


def test_plain_text_round_trips():
    body = letter.assemble_body("Zave", ["You built a layer on top of shopping."])
    assert to_text(from_text(body)) == body


# --- is there anything to send as HTML? ---------------------------------


def test_a_letter_nobody_formatted_has_no_formatting():
    body = letter.assemble_body("Zave", ["You built it."])
    assert not has_formatting(from_text(body))


def test_one_bold_word_is_formatting():
    assert has_formatting("<p>A <strong>bold</strong> word.</p>")
    assert has_formatting("<ul><li>a list</li></ul>")


# --- swapping one paragraph keeps the rest ------------------------------


def test_an_unchanged_paragraph_keeps_its_formatting_when_another_is_swapped():
    old = "<p><strong>Opener</strong> here.</p><p>The company line.</p><p>The ask.</p>"
    new_text = "Opener here.\n\nA different company line.\n\nThe ask."
    carried = carry_formatting(old, new_text)
    assert "<strong>Opener</strong>" in carried
    assert "<p>A different company line.</p>" in carried
    assert to_text(carried) == new_text


def test_nothing_to_carry_when_nothing_was_formatted():
    assert carry_formatting(None, "a\n\nb") is None
    assert carry_formatting("<p>a</p><p>b</p>", "a\n\nc") is None


def test_formatting_on_the_swapped_paragraph_itself_is_gone():
    # It is new text; bold that belonged to the old words does not know
    # where to go in the new ones.
    old = "<p>Opener.</p><p><em>The company line.</em></p>"
    assert carry_formatting(old, "Opener.\n\nA new line.") is None
