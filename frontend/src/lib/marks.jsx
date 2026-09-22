// Wrap the words a check objects to, and leave the rest alone.
//
// Operator request (2026-09-22): "mark the email with a yellowish
// highlighted part where there is an issue and it gives suggestion like
// grammarly", then "i want when i hover over it, it shows suggestion just
// like grammarly use sarvam for suggestion".
//
// The backend resolves each note to a verbatim substring of the BODY
// (app/domain/highlight.py). The screen shows the WIRE TEXT - the body
// plus a greeting, a signature and the opt-out line - so a quote is
// located in whatever text is passed here, and a quote that is not found
// is skipped silently. Marking the wrong words would be worse than
// marking none.
//
// The hover card is the second half of that: the note says what is
// wrong, and the rewrite (POST /api/outreach/{id}/suggest, Sarvam) says
// what to write instead. The rewrite arrives later than the highlight and
// may never arrive at all - an unconfigured key, a model with nothing
// better to offer - so the card is built to read correctly with just the
// note in it.
import { Fragment } from 'react'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'

function Card({ hit, suggestion, pending, onApply }) {
  const deletes = suggestion && suggestion.replacement.trim() === ''
  return (
    <div className="grid gap-2 font-sans">
      <p className="text-sm leading-snug font-medium">{hit.message}</p>

      {suggestion ? (
        <>
          <div className="rounded-md border border-amber-400/70 bg-amber-50 p-2 text-sm dark:border-amber-500/40 dark:bg-amber-950/40">
            {deletes ? (
              <span className="text-muted-foreground italic">Cut this entirely.</span>
            ) : (
              suggestion.replacement
            )}
          </div>
          {suggestion.why && (
            <p className="text-muted-foreground text-xs leading-snug">{suggestion.why}</p>
          )}
          {onApply && (
            <Button size="sm" className="w-fit" onClick={() => onApply(hit.index)}>
              {deletes ? 'Cut it' : 'Use this'}
            </Button>
          )}
        </>
      ) : pending ? (
        <p className="text-muted-foreground flex items-center gap-2 text-xs">
          <Spinner className="size-3" />
          Looking for a better line&hellip;
        </p>
      ) : (
        <p className="text-muted-foreground text-xs leading-snug">
          No rewrite for this one &mdash; it is yours to judge.
        </p>
      )}
    </div>
  )
}

/**
 * `text`: what is on screen (the wire text).
 * `highlights`: `[{message, quote}]` from the API; entries with no quote
 *   are ignored here and still listed in the suggestions panel.
 * `onSelect(index)`: called when a mark is clicked, so the panel can say
 *   which note this is.
 * `suggestions`: `{[index]: {replacement, why}}` from /suggest, keyed by
 *   position in `highlights` - so a rewrite can never attach to the wrong
 *   note.
 * `pending`: the rewrites are still on their way.
 * `onApply(index)`: accept the rewrite for that note.
 *
 * Returns an array of strings and <mark> elements, in order.
 */
export function markUp(
  text,
  highlights = [],
  { onSelect, activeIndex, suggestions = {}, pending = false, onApply } = {},
) {
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
    const suggestion = suggestions[hit.index]
    pieces.push(
      <HoverCard key={`${hit.index}-${hit.start}`} openDelay={120} closeDelay={120}>
        <HoverCardTrigger asChild>
          <mark
            data-note={hit.message}
            // No `title`: the browser's own tooltip would race the hover
            // card and cover it. Screen readers get the note instead.
            aria-label={hit.message}
            onClick={onSelect ? () => onSelect(hit.index) : undefined}
            className={
              'cursor-pointer rounded-sm px-0.5 decoration-amber-500 decoration-2 underline-offset-4 ' +
              (activeIndex === hit.index
                ? 'bg-amber-300/80 underline dark:bg-amber-500/50'
                : 'bg-amber-200/70 dark:bg-amber-400/25')
            }
          >
            {body.slice(hit.start, hit.end)}
          </mark>
        </HoverCardTrigger>
        <HoverCardContent align="start" className="w-80">
          <Card hit={hit} suggestion={suggestion} pending={pending} onApply={onApply} />
        </HoverCardContent>
      </HoverCard>,
    )
    cursor = hit.end
  })
  if (cursor < body.length) pieces.push(body.slice(cursor))

  return pieces.map((piece, i) =>
    typeof piece === 'string' ? <Fragment key={`t${i}`}>{piece}</Fragment> : piece,
  )
}
