// Task 16 (+ fix rounds 1 and 2, coordinator rulings T16-2..T16-5): the
// Review Queue - the screen this whole project exists for. Every
// outreach email is drafted by an LLM, validated deterministically, and
// MUST be approved by a human before it can be sent; this is where that
// gate happens, keyboard- and mouse-driven, for ~270 drafts in a row.
//
// The current item is identified by `outreach_id`, never by list
// position. A real approve invalidates `['queue']`; the backend then
// drops the approved row and the list shifts left, so "index + 1" points
// at the wrong draft the moment a refetch lands (fix round 1 finding
// F1). Tracking `currentId` instead - and computing "what comes next"
// from the *rendered* list at the moment of the action, before the
// mutation fires - means a shrinking refetch can never skip a draft, and
// there is exactly one source of truth for "the item on screen" (F2):
// every handler acts on `current`, derived from the same `items` the DOM
// was built from. Nothing here reads the query cache directly.
//
// Fix round 2 (T16-5, finding F9): the mutation resolving is not the
// same moment as the *refetch* it triggers resolving. In that window, an
// item this screen already approved/rejected/redrafted is still sitting
// in `items` (the query hasn't refreshed yet), and both `idAfter`'s
// "fall back to the previous item" and `current`'s "fall back to the
// first item" branches could re-surface it as the live, actionable
// draft - Approve enabled, ready to be pressed again. `settledIds` closes
// that window: every id this screen has dispatched is filtered out of
// the *rendered* list immediately (optimistically, before the network
// call even resolves), so a stale, already-acted-on draft can never be
// shown as current again, regardless of how slow the refetch is.
import { useEffect, useState } from 'react'
import {
  useApprove,
  useCheckDraft,
  useEdit,
  useQueue,
  useRedraft,
  useReject,
} from '../api/hooks'
import { useKeyboard } from '../hooks/useKeyboard'
import { markUp } from '@/lib/marks'
import { Lightbulb, RefreshCw } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Item, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'

// app/domain/letter.py's USER_FIT_MARKER, mirrored by hand - there is no
// endpoint that exposes it. A draft to a company the sender could
// plausibly be a user of carries a paragraph only he can write; `send`
// refuses a body that still has the marker, so saying so here is what
// stops him approving 47 drafts and finding out later.
const USER_FIT_MARKER = '>>> YOUR TURN'

// The source kinds are database enum values (`long_description`,
// `job_posting`). Nobody reviewing a letter thinks in those terms, so
// say where the fact came from in the words a person would use.
const SOURCE_KINDS = {
  news: 'From a news story about them',
  launch: 'From their launch post',
  job_posting: 'From a job they posted',
  long_description: 'From their own description of the company',
}

// T13-1: `critic_verdict` is PASS / NEEDS_REVISION / ERROR (the critic
// itself crashed). ERROR must never read as a verdict on the letter.
function criticCheck(verdict) {
  if (verdict === 'PASS') return { variant: 'secondary', label: 'Second opinion: reads well' }
  if (verdict === 'NEEDS_REVISION')
    return { variant: 'outline', label: 'Second opinion: worth another look' }
  if (verdict === 'ERROR') return { variant: 'outline', label: 'Second opinion unavailable' }
  return { variant: 'outline', label: 'No second opinion yet' }
}

// The id that was next to `id` in `list` at the moment of an action -
// "next" if one exists, else "previous" (so acting on the last item in
// the list falls back to the one before it rather than jumping past the
// end). `list` is the *pre-mutation* snapshot, captured synchronously
// before `mutate()` is called, so this never depends on what the refetch
// eventually returns. Fix round 2: callers pass the already
// settled-filtered list, so neither branch can land back on a draft this
// screen just dispatched.
function idAfter(list, id) {
  const idx = list.findIndex((entry) => entry.outreach_id === id)
  if (idx === -1) return null
  if (idx + 1 < list.length) return list[idx + 1].outreach_id
  if (idx - 1 >= 0) return list[idx - 1].outreach_id
  return null
}

export default function ReviewQueue() {
  const queueQuery = useQueue()
  const approve = useApprove()
  const edit = useEdit()
  const reject = useReject()
  const redraft = useRedraft()
  const checkDraft = useCheckDraft({
    onSuccess: (data, { id }) => {
      setCheckNotes(data?.notes ?? [])
      setCheckHighlights(data?.highlights ?? [])
      setCheckedId(id)
      setActiveNote(null)
    },
  })

  const items = queueQuery.data ?? []

  const [currentId, setCurrentId] = useState(null)
  const [isEditing, setIsEditing] = useState(false)
  const [editErrors, setEditErrors] = useState([])
  const [actionError, setActionError] = useState(null)
  const [dismissedIds, setDismissedIds] = useState(() => new Set())
  // T16-5/F9: ids this screen has dispatched an approve/reject/redraft
  // for, filtered out of every rendered view of the queue until the
  // backend confirms (a redraft sends the row back to READY_TO_DRAFT,
  // not out of the queue forever, but it's still gone from
  // PENDING_APPROVAL - and therefore from `pending()` - the moment this
  // screen has acted, so the same filter is correct for all three
  // actions). A failed action removes its id again immediately.
  const [settledIds, setSettledIds] = useState(() => new Set())
  // The result of the operator pressing "Re-check", and which draft it
  // belongs to.
  const [checkNotes, setCheckNotes] = useState([])
  const [checkedId, setCheckedId] = useState(null)
  // The notes paired with the words they are about, and which one the
  // operator last clicked on - in the letter or in the panel.
  const [checkHighlights, setCheckHighlights] = useState([])
  const [activeNote, setActiveNote] = useState(null)

  // Prune settled ids the backend has already confirmed (no longer in
  // `items` at all) - once gone, holding onto the id serves no purpose.
  // This adjusts state during render rather than in an effect (see
  // "Adjusting state based on a prop" in the React docs): it is a pure
  // function of `items`/`settledIds`, converges in the same render pass
  // (the next render finds nothing left to prune), and needs no
  // synchronization with anything outside React.
  const stalePrunable = [...settledIds].filter(
    (id) => !items.some((it) => it.outreach_id === id),
  )
  if (stalePrunable.length > 0) {
    setSettledIds((prev) => {
      const next = new Set(prev)
      for (const id of stalePrunable) next.delete(id)
      return next
    })
  }

  const visibleItems = items.filter((it) => !settledIds.has(it.outreach_id))

  // Resolve the displayed item by id, derived during render rather than
  // synced via an effect (an effect that calls setState on every `items`
  // change just to "fix up" a derived value is a cascading-render smell,
  // and unnecessary here): if `currentId` matches something in the
  // visible list, show that; otherwise (nothing selected yet on first
  // load, or the selected id fell out of the list for a reason other
  // than this screen's own action) fall back to the first visible item.
  //
  // F11: the fallback-to-first-item branch is reachable in the wild only
  // when something *other* than this screen's own action removes the
  // currently-viewed id mid-session - concurrently reviewing the same
  // outreach row from the CLI is the one real path. Jumping to the start
  // of the (now-shorter) list is deliberate: it is rendered before any
  // key can act on stale data, so there is no wrong-item risk.
  const current =
    visibleItems.find((it) => it.outreach_id === currentId) ??
    (visibleItems.length > 0 ? visibleItems[0] : null)
  const position = current
    ? visibleItems.findIndex((it) => it.outreach_id === current.outreach_id) + 1
    : 0

  // T16-4/F5: one in-flight approve/reject/redraft at a time - "aa" must
  // never double-submit the same action. Keyed on the mutations
  // themselves since only one item can be acted on at once from this
  // screen anyway, and this also covers the narrow window (two keydowns
  // in the same tick, before React has re-rendered with the new
  // `currentId`/`settledIds`) that filtering alone can't:
  // `mutation.isPending` flips true synchronously inside `mutate()`,
  // before any state update commits.
  const busy = approve.isPending || reject.isPending || redraft.isPending

  // T16-4/F10: navigation is gated on `busy` too, so Skip/next/previous
  // are exactly as inert as the (visibly disabled) action buttons while
  // a mutation is in flight - key and click stay indistinguishable
  // (T16-1).
  function goNext() {
    if (!current || busy) return
    const idx = visibleItems.findIndex((it) => it.outreach_id === current.outreach_id)
    const next = visibleItems[idx + 1]
    if (next) setCurrentId(next.outreach_id)
  }

  function goPrev() {
    if (!current || busy) return
    const idx = visibleItems.findIndex((it) => it.outreach_id === current.outreach_id)
    const prev = visibleItems[idx - 1]
    if (prev) setCurrentId(prev.outreach_id)
  }

  // Shared shape for approve/reject/redraft: mark the acted-on id
  // settled and advance immediately (so the operator never waits on the
  // network mid-review), to the id that was next to it in *this*
  // render's visible list (F1) - roll back (un-settle, restore
  // `currentId`) and surface the failure if the mutation turns out to
  // have failed (F4).
  function actOn(mutation, verb) {
    if (!current || busy) return
    const actedId = current.outreach_id
    const nextId = idAfter(visibleItems, actedId)
    setActionError(null)
    setSettledIds((prev) => new Set(prev).add(actedId))
    setCurrentId(nextId)
    mutation.mutate(actedId, {
      onError: (err) => {
        setSettledIds((prev) => {
          const next = new Set(prev)
          next.delete(actedId)
          return next
        })
        setCurrentId(actedId)
        setActionError(`Could not ${verb}: ${err.message}`)
      },
    })
  }

  function handleApprove() {
    actOn(approve, 'approve')
  }

  function handleReject() {
    actOn(reject, 'reject')
  }

  function handleRedraft() {
    actOn(redraft, 'redraft')
  }

  function handleSkip() {
    if (busy) return
    setActionError(null)
    goNext()
  }

  function openEditor() {
    if (!current || busy) return
    setEditErrors([])
    setActionError(null)
    setIsEditing(true)
  }

  function closeEditor() {
    setIsEditing(false)
    setEditErrors([])
  }

  function saveEdit(subject, body) {
    if (!current) return
    edit.mutate(
      { id: current.outreach_id, subject, body },
      {
        // 2026-09-22: a save always saves. The checks come back as notes
        // about what was just written, so the editor closes and the
        // suggestions panel shows them - it no longer refuses.
        onSuccess: (data) => {
          setIsEditing(false)
          setEditErrors([])
          setCheckNotes(data?.notes ?? [])
          setCheckHighlights(data?.highlights ?? [])
          setCheckedId(current.outreach_id)
          setActiveNote(null)
        },
        // A real failure (the row vanished, the API is down). The editor
        // stays open holding whatever the operator typed.
        onError: (err) => {
          setEditErrors(err.errors ?? [err.message])
        },
      },
    )
  }

  function dismissCritic() {
    if (!current) return
    setDismissedIds((prev) => new Set(prev).add(current.outreach_id))
  }

  // The checks run as soon as a draft is shown, so the letter is marked
  // up on arrival rather than after pressing a button - which is what
  // "like grammarly" means. It is a cheap call: pure computation over
  // text the server already has, no model involved.
  //
  // Keyed on the id alone, so moving to the next letter re-runs it and
  // staying put does not. The mutation object is deliberately NOT a
  // dependency: it changes identity on every state transition of its own,
  // which would loop.
  const currentIdForCheck = current?.outreach_id ?? null
  useEffect(() => {
    if (currentIdForCheck == null) return
    checkDraft.mutate({ id: currentIdForCheck })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIdForCheck])

  // T16-3/F3: ALL review-queue action keys are suspended while the
  // editor is open - not merely inert inside its own text fields.
  // Tabbing from the body textarea to Save/Cancel is the normal keyboard
  // path, and a stray "a" or "j" landing on one of those buttons must
  // never approve or switch drafts out from under an open edit.
  useKeyboard(
    {
      a: handleApprove,
      e: openEditor,
      d: handleRedraft,
      r: handleReject,
      s: handleSkip,
      j: goNext,
      k: goPrev,
    },
    { enabled: !isEditing },
  )

  // F6: Escape cancels the editor - the inverse suspension (only live
  // while editing), and it must fire even with focus still inside the
  // body textarea (the normal way an operator backs out), so it opts out
  // of the input-focus guard.
  useKeyboard(
    { Escape: { handler: closeEditor, bypassFocusGuard: true } },
    { enabled: isEditing },
  )

  if (queueQuery.isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (queueQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load queue</AlertTitle>
        <AlertDescription>{queueQuery.error.message}</AlertDescription>
      </Alert>
    )
  }

  if (items.length === 0) {
    return (
      <>
        <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Queue is empty</EmptyTitle>
            <EmptyDescription>Nothing left to review.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </>
    )
  }

  // T16-5/F9: every remaining item is one this screen has already
  // dispatched an action for - the refetch just hasn't landed yet. Not
  // the same message as "genuinely nothing left" (`items.length === 0`
  // above): there may well be more items once the confirmed list comes
  // back, this is purely "nothing safe to show right now."
  if (visibleItems.length === 0) {
    return (
      <>
        <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Nothing left to review</EmptyTitle>
            <EmptyDescription>Drafts you approved are waiting to send.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </>
    )
  }

  // `current` always resolves to a real item once `visibleItems` is
  // non-empty (see its derivation above) - this is just a defensive
  // fallback, never expected to render.
  if (!current) return <Skeleton className="h-96 w-full" />

  const progress = Math.round((position / visibleItems.length) * 100)
  const needsYourParagraph = (current.wire_text ?? '').includes(USER_FIT_MARKER)
  const critic = criticCheck(current.critic_verdict)
  const showCriticNote = Boolean(current.critic_note) && !dismissedIds.has(current.outreach_id)
  // A fresh re-check wins over the note stored at draft time, but only
  // for the draft it was run on - otherwise moving to the next letter
  // would show it the previous one's verdict.
  const suggestions =
    checkedId === current.outreach_id
      ? checkNotes
      : (current.last_error ? [current.last_error] : [])
  // The critic's note counts as a suggestion: it is one more thing the
  // machine noticed, and the panel is tinted when there is anything at
  // all to read.
  // Only a fresh check knows WHERE each note applies; the note stored at
  // draft time is just a sentence. So the letter is marked up after a
  // re-check or a save, and reads plainly before that.
  const highlights = checkedId === current.outreach_id ? checkHighlights : []
  const hasSuggestions = suggestions.length > 0 || showCriticNote
  const suggestionCount = suggestions.length + (showCriticNote ? 1 : 0)

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
        <div className="mt-2 flex items-center gap-3">
          <span className="text-muted-foreground text-sm tabular-nums whitespace-nowrap">
            {position} of {visibleItems.length}
          </span>
          <Progress value={progress} className="max-w-md" />
        </div>
      </div>

      {actionError && (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}

      {needsYourParagraph && !isEditing && (
        <Alert>
          <AlertTitle>This one is waiting on you</AlertTitle>
          <AlertDescription>
            Press <kbd className="font-mono">e</kbd> and replace the{' '}
            <span className="font-mono">{USER_FIT_MARKER}</span> paragraph with the real thing,
            or delete it. Approving it as it stands will not send.
          </AlertDescription>
        </Alert>
      )}

      {isEditing ? (
        <EditDraft
          key={current.outreach_id}
          initialSubject={current.subject ?? ''}
          initialBody={current.body ?? ''}
          errors={editErrors}
          saving={edit.isPending}
          onSave={saveEdit}
          onCancel={closeEditor}
        />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
          {/* The letter. Ruling R2: this renders `wire_text` (greeting,
              signature and opt-out line already applied), never the bare
              stored `body` - the operator reviews what actually goes
              out. */}
          <Card aria-label="Draft email">
            <CardHeader>
              <CardDescription>To</CardDescription>
              <CardTitle className="text-base font-normal">
                {current.to ?? 'no address yet'}
              </CardTitle>
              <CardDescription className="pt-2">Subject</CardDescription>
              <CardTitle className="text-base">{current.subject ?? '(no subject)'}</CardTitle>
              {current.sequence_step > 0 && (
                <Badge variant="secondary" className="mt-2 w-fit">
                  Follow-up number {current.sequence_step}
                </Badge>
              )}
            </CardHeader>
            <Separator />
            <CardContent>
              <pre className="font-sans text-[15px] leading-relaxed whitespace-pre-wrap">
                {highlights.length > 0
                  ? markUp(current.wire_text, highlights, {
                      onSelect: setActiveNote,
                      activeIndex: activeNote,
                    })
                  : current.wire_text}
              </pre>
            </CardContent>
            <CardFooter>
              {/* Live finding (2026-09-16): the operator reviewed a draft
                  and said "there is no resume attached". It is - `send`
                  attaches RESUME_PATH - but nothing said so.
                  `attachment_name` is null exactly when `send` would
                  refuse, so say that instead. */}
              {current.attachment_name ? (
                <p className="text-muted-foreground text-xs">
                  {current.attachment_name} goes with it · {current.word_count} words
                </p>
              ) : (
                <p className="text-destructive text-xs" role="alert">
                  No resume file configured, so this cannot be sent yet · {current.word_count}{' '}
                  words
                </p>
              )}
            </CardFooter>
          </Card>

          {/* The docket: everything you need to judge the letter without
              leaving it. Sticky beside it on a wide screen, so the
              evidence stays put while you read the claim. */}
          <aside
            className="grid gap-4 lg:sticky lg:top-4"
            aria-label="Company, source and checks"
          >
            {/* Operator decision (2026-09-22): these are SUGGESTIONS, not
                a gate. They used to shout "Needs a fix before approving"
                in red, which read as a refusal - nothing actually stops
                the approve, and it is his letter and his name.

                But quiet is not the same as advisory: the first version
                of this card was so recessive it went unread at the
                bottom of the docket. It sits FIRST now, and when there
                is something to say it is tinted amber and carries the
                count. Amber, not red: red is for the failures elsewhere
                in this app, and this is the one panel that is neither a
                failure nor a refusal. With nothing flagged it fades back
                to an ordinary card, so the colour means "there is
                something here" rather than being permanent decoration. */}
            <Card
              className={
                hasSuggestions
                  ? 'border-amber-400/70 bg-amber-50/80 dark:border-amber-500/40 dark:bg-amber-950/25'
                  : undefined
              }
            >
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Lightbulb
                    className={hasSuggestions ? 'size-4 text-amber-600 dark:text-amber-400' : 'size-4'}
                  />
                  AI suggestions
                  {hasSuggestions && (
                    <Badge
                      variant="secondary"
                      className="bg-amber-200/70 text-amber-900 dark:bg-amber-900/50 dark:text-amber-100"
                    >
                      {suggestionCount}
                    </Badge>
                  )}
                </CardTitle>
                <CardDescription>
                  What the checks noticed. Advice only &mdash; you can approve anyway.
                </CardDescription>
                <CardAction>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={checkDraft.isPending}
                    onClick={() => checkDraft.mutate({ id: current.outreach_id })}
                  >
                    {checkDraft.isPending ? <Spinner /> : <RefreshCw />}
                    Re-check
                  </Button>
                </CardAction>
              </CardHeader>
              <CardContent className="grid gap-3">
                <Badge variant={critic.variant} className="w-fit">
                  {critic.label}
                </Badge>

                {suggestions.length > 0 ? (
                  <ul className="grid gap-1.5 text-sm">
                    {suggestions.map((note, index) => {
                      const marked = Boolean(highlights[index]?.quote)
                      return (
                        <li key={note}>
                          <button
                            type="button"
                            disabled={!marked}
                            onClick={() => setActiveNote(index)}
                            className={
                              'w-full rounded px-1.5 py-1 text-left font-medium ' +
                              (marked ? 'hover:bg-amber-200/60 dark:hover:bg-amber-900/40 ' : '') +
                              (activeNote === index ? 'bg-amber-200/70 dark:bg-amber-900/50' : '')
                            }
                          >
                            {note}
                            {/* Says which notes point at actual words, so
                                a note with nothing marked does not look
                                like a broken highlight. */}
                            {marked && (
                              <span className="text-muted-foreground ml-1 text-xs">
                                &mdash; highlighted
                              </span>
                            )}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                ) : (
                  <p className="text-muted-foreground text-sm">
                    {checkedId === current.outreach_id
                      ? 'Nothing flagged on the last check.'
                      : 'Nothing flagged.'}
                  </p>
                )}

                {showCriticNote && (
                  <div className="grid gap-2 border-l-2 border-amber-400/70 pl-3 dark:border-amber-500/40">
                    {/* Not muted: this is the second opinion on the
                        letter, and unreadable advice is the same as no
                        advice. */}
                    <p className="text-sm">{current.critic_note}</p>
                    <Button variant="ghost" size="sm" className="w-fit" onClick={dismissCritic}>
                      Dismiss
                    </Button>
                  </div>
                )}

                {checkDraft.isError && (
                  <p className="text-destructive text-xs" role="alert">
                    Could not re-check: {checkDraft.error.message}
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{current.company_name ?? 'Unknown company'}</CardTitle>
                {current.company_one_liner && (
                  <CardDescription>{current.company_one_liner}</CardDescription>
                )}
              </CardHeader>
              <CardContent>
                <Item variant="muted">
                  <ItemContent>
                    <ItemDescription>Founder</ItemDescription>
                    <ItemTitle>
                      {current.founder_name}
                      {current.founder_title ? ` — ${current.founder_title}` : ''}
                    </ItemTitle>
                  </ItemContent>
                </Item>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">What the letter is built on</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-1">
                {current.source_text ? (
                  <>
                    <p className="text-muted-foreground text-xs">
                      {SOURCE_KINDS[current.source_kind] ?? 'From their research material'}
                    </p>
                    <p className="text-sm">{current.source_text}</p>
                    {current.source_publication && (
                      <p className="text-muted-foreground text-xs">
                        {current.source_publication}
                      </p>
                    )}
                  </>
                ) : (
                  <p className="text-muted-foreground text-sm">
                    Nothing cited — this one was written by hand.
                  </p>
                )}
              </CardContent>
            </Card>

          </aside>
        </div>
      )}

      {/* The decision rail, pinned to the bottom of the viewport: a
          250-word letter used to push Approve off screen, so the
          operator had to scroll back up to act on what they had just
          finished reading. Each button dispatches through the exact same
          handler its key does (T16-1). */}
      {!isEditing && (
        <div
          role="group"
          aria-label="Review actions"
          className="bg-background sticky bottom-0 -mx-4 flex flex-wrap gap-2 border-t px-4 py-3 md:-mx-6 md:px-6"
        >
          <Button onClick={handleApprove} disabled={busy}>
            Approve <kbd className="ml-1 text-xs opacity-70">a</kbd>
          </Button>
          <Button variant="outline" onClick={openEditor} disabled={busy}>
            Edit <kbd className="ml-1 text-xs opacity-70">e</kbd>
          </Button>
          <Button variant="outline" onClick={handleRedraft} disabled={busy}>
            Redraft <kbd className="ml-1 text-xs opacity-70">d</kbd>
          </Button>
          <Button variant="outline" onClick={handleReject} disabled={busy}>
            Reject <kbd className="ml-1 text-xs opacity-70">r</kbd>
          </Button>
          <Button variant="ghost" onClick={handleSkip} disabled={busy}>
            Skip <kbd className="ml-1 text-xs opacity-70">s</kbd>
          </Button>
        </div>
      )}
    </>
  )
}

// The manual-write / edit form. Subject + body are local, controlled
// state seeded once from the props - deliberately NOT reset when
// `errors` changes, so a 422 from the validator never wipes out what the
// operator typed. The parent closes this only on a successful save, and
// re-mounts it (via `key`) only when a different draft is opened.
function EditDraft({ initialSubject, initialBody, errors, saving, onSave, onCancel }) {
  const [subject, setSubject] = useState(initialSubject ?? '')
  const [body, setBody] = useState(initialBody ?? '')

  return (
    <Card aria-label="Edit draft">
      <CardHeader>
        <CardTitle>Edit draft</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {errors && errors.length > 0 && (
          <Alert variant="destructive" role="alert">
            <AlertTitle>This draft was not saved</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-4">
                {errors.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        <div className="grid gap-2">
          <Label htmlFor="edit-subject">Subject</Label>
          <Input
            id="edit-subject"
            aria-label="Subject"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="edit-body">Body</Label>
          <Textarea
            id="edit-body"
            aria-label="Body"
            rows={14}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        </div>
      </CardContent>
      <CardFooter className="gap-2">
        <Button onClick={() => onSave(subject, body)} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </CardFooter>
    </Card>
  )
}
