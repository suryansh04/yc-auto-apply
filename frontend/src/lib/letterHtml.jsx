// A formatted letter, rendered as React - with the checks' highlights and
// the company paragraph's hover card still on it.
//
// Operator request (2026-09-23): bold, italic, text size and lists. The
// review card used to render `wire_text` in a <pre>; a formatted letter
// has to show its formatting, or the operator approves something he has
// not seen. But the marks (amber checks, the sky company paragraph with
// its AI / Generic / Write-it tabs) are the review screen's whole point,
// and they work on TEXT.
//
// So the HTML is walked here and every text node goes through `markUp` on
// its own. A quote that lies inside one text node - the company
// paragraph, almost every check - is marked exactly as before; one that
// crosses a formatting boundary (half a sentence bold) is not found in
// either node and is skipped, the same rule `markUp` already follows for
// any quote it cannot find. Marking the wrong words would be worse.
//
// The HTML arrives sanitised by the server (`app/domain/rich_text.py`).
// This walk is a second allowlist anyway: only these tags become
// elements, only `font-size` survives as a style, and nothing is ever set
// as raw HTML.
import { createElement } from 'react'
import { markUp } from './marks'

const TAGS = new Set(['p', 'br', 'strong', 'em', 'u', 'ul', 'ol', 'li', 'span'])

function convert(node, key, highlights, options) {
  if (node.nodeType === 3) {
    const text = node.nodeValue ?? ''
    return highlights.length > 0 ? markUp(text, highlights, options) : text
  }
  if (node.nodeType !== 1) return null
  const tag = node.tagName.toLowerCase()
  const children = Array.from(node.childNodes).map((child, i) =>
    convert(child, `${key}.${i}`, highlights, options),
  )
  if (!TAGS.has(tag)) return createElement('span', { key }, ...children)
  if (tag === 'br') return createElement('br', { key })
  const props = { key }
  if (tag === 'span') {
    const size = node.style?.fontSize
    if (size) props.style = { fontSize: size }
  }
  return createElement(tag, props, ...children)
}

export function renderLetterHtml(html, highlights = [], options = {}) {
  const doc = new DOMParser().parseFromString(html ?? '', 'text/html')
  return Array.from(doc.body.childNodes).map((node, i) =>
    convert(node, `n${i}`, highlights, options),
  )
}

/** The classes a formatted letter needs: Tailwind's reset removes list
 *  markers and paragraph spacing, which a letter depends on. */
export const LETTER_HTML_CLASSES =
  'font-sans text-[15px] leading-relaxed [&_p]:my-4 first:[&_p]:mt-0 ' +
  '[&_ul]:my-4 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:my-4 [&_ol]:list-decimal [&_ol]:pl-6'
