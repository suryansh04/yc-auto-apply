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
 * The company paragraph - the one part of the letter that is about the
 * founder rather than about the sender, and the only part the generic
 * line would replace.
 *
 * Operator request (2026-09-23): "no highlight that section to". The
 * generic-line button under the letter had to be taken on trust; marking
 * the paragraph is what makes the swap show its work, because the text
 * that would disappear is on screen next to the text that would take its
 * place.
 */
function InsightCard({ hit }) {
  return (
    <div className="grid gap-2 font-sans">
      <p className="text-sm leading-snug font-medium">{hit.message}</p>
      {hit.generic && (
        <>
          <p className="text-muted-foreground text-xs leading-snug">
            Can&rsquo;t find an honest specific line? This replaces it:
          </p>
          <div className="rounded-md border border-sky-400/70 bg-sky-50 p-2 text-sm dark:border-sky-500/40 dark:bg-sky-950/40">
            {hit.generic}
          </div>
          {hit.onUseGeneric && (
            <Button
              size="sm"
              variant="outline"
              className="w-fit"
              disabled={hit.busy}
              onClick={hit.onUseGeneric}
            >
              {hit.busy ? <Spinner /> : null}
              Use a generic line
            </Button>
          )}
        </>
      )}
    </div>
  )
}

/**
 * `text`: what is on screen (the wire text).
 * `highlights`: `[{message, quote}]` from the API; entries with no quote
 *   are ignored here and still listed in the suggestions panel. An entry
 *   may carry `kind: 'insight'` - the company paragraph rather than a
 *   check - which is marked in sky, never amber, and always loses an
 *   overlap to a check.
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

  // A check mark always beats the company-paragraph mark. They overlap by
  // design - a hook note IS about that paragraph - and a mark inside a
  // mark renders as neither. Something wrong with the words is more
  // urgent than "this is the part about them", and the swap stays
  // reachable from the button under the letter either way.
  const accepted = []
  const taken = []
  const byPrecedence = [
    ...found.filter((h) => h.kind !== 'insight'),
    ...found.filter((h) => h.kind === 'insight'),
  ]
  byPrecedence.forEach((hit) => {
    // A quote can also appear twice in the wire text; taking the first
    // free position is the same rule in both cases.
    if (taken.some(([start, end]) => hit.start < end && start < hit.end)) return
    taken.push([hit.start, hit.end])
    accepted.push(hit)
  })
  accepted.sort((a, b) => a.start - b.start)

  const pieces = []
  let cursor = 0
  accepted.forEach((hit) => {
    if (hit.start > cursor) pieces.push(body.slice(cursor, hit.start))
    const insight = hit.kind === 'insight'
    pieces.push(
      <HoverCard key={`${hit.index}-${hit.start}`} openDelay={120} closeDelay={120}>
        <HoverCardTrigger asChild>
          <mark
            data-note={hit.message}
            data-kind={hit.kind ?? 'check'}
            // No `title`: the browser's own tooltip would race the hover
            // card and cover it. Screen readers get the note instead.
            aria-label={hit.message}
            onClick={onSelect ? () => onSelect(hit.index) : undefined}
            className={
              'cursor-pointer rounded-sm px-0.5 decoration-2 underline-offset-4 ' +
              // Sky, not amber, for the company paragraph: amber means
              // "something here is wrong", and this mark means "this is
              // the part that is about them". Two colours for two
              // different claims.
              (insight
                ? 'bg-sky-100 decoration-sky-500 decoration-dotted underline dark:bg-sky-400/20 '
                : 'decoration-amber-500 ') +
              (insight
                ? ''
                : activeIndex === hit.index
                  ? 'bg-amber-300/80 underline dark:bg-amber-500/50'
                  : 'bg-amber-200/70 dark:bg-amber-400/25')
            }
          >
            {body.slice(hit.start, hit.end)}
          </mark>
        </HoverCardTrigger>
        <HoverCardContent align="start" className={insight ? 'w-96' : 'w-80'}>
          {insight ? (
            <InsightCard hit={hit} />
          ) : (
            <Card hit={hit} suggestion={suggestions[hit.index]} pending={pending} onApply={onApply} />
          )}
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
