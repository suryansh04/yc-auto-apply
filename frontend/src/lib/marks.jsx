// Wrap the words a check objects to, and leave the rest alone.
//
// Operator request (2026-09-22): "mark the email with a yellowish
// highlighted part where there is an issue and it gives suggestion like
// grammarly".
//
// The backend resolves each note to a verbatim substring of the BODY
// (app/domain/highlight.py). The screen shows the WIRE TEXT - the body
// plus a greeting, a signature and the opt-out line - so a quote is
// located in whatever text is passed here, and a quote that is not found
// is skipped silently. Marking the wrong words would be worse than
// marking none.
import { Fragment } from 'react'

/**
 * `text`: what is on screen (the wire text).
 * `highlights`: `[{message, quote}]` from the API; entries with no quote
 *   are ignored here and still listed in the suggestions panel.
 * `onSelect(index)`: called when a mark is clicked, so the panel can say
 *   which note this is.
 *
 * Returns an array of strings and <mark> elements, in order.
 */
export function markUp(text, highlights = [], { onSelect, activeIndex } = {}) {
  const body = text ?? ''
  // Resolve every quote to a position first, so overlaps and misses are
  // settled before anything is rendered.
  const found = []
  highlights.forEach((highlight, index) => {
    if (!highlight?.quote) return
    const start = body.indexOf(highlight.quote)
    if (start < 0) return
    found.push({ start, end: start + highlight.quote.length, index, ...highlight })
  })
  found.sort((a, b) => a.start - b.start)

  const pieces = []
  let cursor = 0
  found.forEach((hit) => {
    // The backend already drops overlaps; this is the belt to that
    // braces, since a quote can also appear twice in the wire text.
    if (hit.start < cursor) return
    if (hit.start > cursor) pieces.push(body.slice(cursor, hit.start))
    pieces.push(
      <mark
        key={`${hit.index}-${hit.start}`}
        data-note={hit.message}
        title={hit.message}
        onClick={onSelect ? () => onSelect(hit.index) : undefined}
        className={
          'cursor-pointer rounded-sm px-0.5 decoration-amber-500 decoration-2 underline-offset-4 ' +
          (activeIndex === hit.index
            ? 'bg-amber-300/80 underline dark:bg-amber-500/50'
            : 'bg-amber-200/70 dark:bg-amber-400/25')
        }
      >
        {body.slice(hit.start, hit.end)}
      </mark>,
    )
    cursor = hit.end
  })
  if (cursor < body.length) pieces.push(body.slice(cursor))

  return pieces.map((piece, i) =>
    typeof piece === 'string' ? <Fragment key={`t${i}`}>{piece}</Fragment> : piece,
  )
}
