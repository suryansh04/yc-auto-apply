// Plain text <-> the HTML the editor starts from.
//
// Mirrors `app/domain/rich_text.py` `from_text`: one <p> per paragraph,
// line breaks kept. It only seeds the editor for a letter nobody has
// formatted yet - the server sanitises whatever comes back and derives
// the plain text itself, so nothing here is trusted.

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function textToHtml(text) {
  return (text ?? '')
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => `<p>${paragraph.split('\n').map(escapeHtml).join('<br>')}</p>`)
    .join('')
}
