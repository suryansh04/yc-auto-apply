"""Formatted letters, end to end through the API (operator request
2026-09-23): "give option such as bolding, italic, increasing size of a
particular text, numerical or bullet points".

The plain text stays canonical. These pin that the server - not the
client - decides what the plain text is, that nothing unsafe is stored,
and that swapping one paragraph does not throw the rest of the
formatting away.
"""
from app.domain import letter


def _row(repos, draft):
    return repos.message.get_outbound(draft.outreach.id, 0)


def _edit(client, draft, **payload):
    payload.setdefault("subject", "Would love to build at Vahan")
    return client.post(f"/api/outreach/{draft.outreach.id}/edit", json=payload)


def test_a_formatted_save_stores_the_html_and_derives_the_text(client, repos, pending_draft):
    r = _edit(client, pending_draft,
              body_html="<p>A <strong>bold</strong> claim.</p><ol><li>one</li><li>two</li></ol>")
    assert r.status_code == 200
    row = _row(repos, pending_draft)
    assert row.body_html == "<p>A <strong>bold</strong> claim.</p><ol><li>one</li><li>two</li></ol>"
    assert row.body == "A bold claim.\n\n1. one\n2. two"


def test_the_plain_text_a_client_sends_beside_the_html_is_ignored(client, repos, pending_draft):
    """A client can never store the plain text and the formatting out of
    step - the checks read one and the founder reads the other."""
    _edit(client, pending_draft, body="something else entirely",
          body_html="<p>What it <em>actually</em> says.</p>")
    assert _row(repos, pending_draft).body == "What it actually says."


def test_nothing_unsafe_is_stored(client, repos, pending_draft):
    _edit(client, pending_draft,
          body_html='<p onclick="x()">Hi <a href="http://evil">there</a></p><script>alert(1)</script>')
    row = _row(repos, pending_draft)
    for bad in ("onclick", "href", "script", "alert"):
        assert bad not in (row.body_html or "")
    assert row.body == "Hi there"


def test_a_letter_with_no_formatting_is_stored_as_plain_text(client, repos, pending_draft):
    # An HTML part that adds nothing is one more thing for a spam filter.
    _edit(client, pending_draft, body_html="<p>Just words.</p><p>More words.</p>")
    row = _row(repos, pending_draft)
    assert row.body_html is None
    assert row.body == "Just words.\n\nMore words."


def test_the_preview_carries_the_formatted_email(client, repos, pending_draft):
    _edit(client, pending_draft, body_html="<p>A <strong>bold</strong> claim.</p>")
    item = client.get(f"/api/queue/{pending_draft.outreach.id}").json()
    assert item["body_html"] == "<p>A <strong>bold</strong> claim.</p>"
    # The whole email: greeting and body, formatted.
    assert item["wire_html"].startswith("<p>Hi Madhav,</p><p>A <strong>bold</strong> claim.</p>")
    assert item["wire_text"].startswith("Hi Madhav,\n\nA bold claim.")


def test_swapping_the_company_paragraph_keeps_the_rest_of_the_formatting(
    client, repos, pending_draft
):
    """The generic-line swap changes one paragraph by plain text. Without
    carrying formatting over, it would silently strip every bold word the
    operator had put anywhere else in the letter."""
    body = letter.assemble_body("Vahan", ["You built a voice layer."])
    html = "".join(
        f"<p><strong>{p}</strong></p>" if p == letter.OPENER else f"<p>{p}</p>"
        for p in body.split("\n\n")
    ).replace("\n", "<br>")
    _edit(client, pending_draft, body_html=html)
    assert "<strong>" in _row(repos, pending_draft).body_html

    client.post(f"/api/outreach/{pending_draft.outreach.id}/generic-line")

    row = _row(repos, pending_draft)
    assert letter.generic_insight("Vahan") in row.body
    assert f"<strong>{letter.OPENER}</strong>" in row.body_html
    assert letter.generic_insight("Vahan") in row.body_html
