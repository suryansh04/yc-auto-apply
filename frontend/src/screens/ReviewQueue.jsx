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
import { useState } from 'react'
import { useApprove, useEdit, useQueue, useRedraft, useReject } from '../api/hooks'
import { useKeyboard } from '../hooks/useKeyboard'
import DraftPane from '../components/DraftPane'
import SourcePane from '../components/SourcePane'
import Editor from '../components/Editor'
import ActionBar from '../components/ActionBar'
import Banner from '../components/Banner'

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
  // The common case (this screen's own approve/reject/redraft) already
  // set `currentId` to the right next id *before* the mutation fired, so
  // this falls through to the first branch.
  //
  // F11: the fallback-to-first-item branch is reachable in the wild only
  // when something *other* than this screen's own action removes the
  // currently-viewed id mid-session - concurrently reviewing the same
  // outreach row from the CLI is the one real path, since nothing else
  // in this app writes to the queue. Jumping to the start of the
  // (now-shorter) list rather than trying to guess a "neighbour" is a
  // deliberate simplicity choice: it is rendered before any key can act
  // on stale data, so there is no wrong-item risk, only a jump the
  // operator will notice and re-navigate from.
  const current =
    visibleItems.find((it) => it.outreach_id === currentId) ??
    (visibleItems.length > 0 ? visibleItems[0] : null)
  const position = current
    ? visibleItems.findIndex((it) => it.outreach_id === current.outreach_id) + 1
    : 0

  // T16-4/F5: one in-flight approve/reject/redraft at a time - "aa" must
  // never double-submit the same action. Keyed on the mutations
  // themselves (rather than per-id) since only one item can be acted on
  // at once from this screen anyway, and this also covers the narrow
  // window (two keydowns in the same tick, before React has re-rendered
  // with the new `currentId`/`settledIds`) that filtering alone can't:
  // `mutation.isPending` flips true synchronously inside `mutate()`,
  // before any state update commits.
  const busy = approve.isPending || reject.isPending || redraft.isPending

  // T16-4/F10: navigation is gated on `busy` too, so Skip/next/previous
  // are exactly as inert as the (visibly disabled) ActionBar buttons
  // while a mutation is in flight - key and click stay indistinguishable
  // (T16-1), and it closes off one more way to land somewhere surprising
  // mid-mutation.
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
        onSuccess: () => {
          setIsEditing(false)
          setEditErrors([])
        },
        // 422 {errors: [...]} - the validator's own messages, verbatim.
        // Nothing was persisted on the backend, and the Editor keeps
        // whatever the operator typed (it owns its own local state).
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

  if (queueQuery.isLoading) return <p>Loading queue...</p>

  if (queueQuery.isError) {
    return <Banner kind="error">Could not load queue: {queueQuery.error.message}</Banner>
  }

  if (items.length === 0) {
    return (
      <div className="review-queue">
        <h1>Review Queue</h1>
        <p className="empty-state">Queue is empty — nothing left to review.</p>
      </div>
    )
  }

  // T16-5/F9: every remaining item is one this screen has already
  // dispatched an action for - the refetch just hasn't landed yet. Not
  // the same message as "genuinely nothing left" (`items.length === 0`
  // above): there may well be more items once the confirmed list comes
  // back, this is purely "nothing safe to show right now."
  if (visibleItems.length === 0) {
    return (
      <div className="review-queue">
        <h1>Review queue</h1>
        <p className="finishing">Nothing left to review. Drafts you approved are waiting to send.</p>
      </div>
    )
  }

  // `current` always resolves to a real item once `visibleItems` is
  // non-empty (see its derivation above) - this is just a defensive
  // fallback, never expected to render.
  if (!current) return <p>Loading queue...</p>

  const progress = Math.round((position / visibleItems.length) * 100)

  return (
    <div className="review-queue">
      <h1>Review queue</h1>
      <div className="queue-head">
        <p className="queue-position">
          {position} of {visibleItems.length}
        </p>
        <div className="queue-progress" aria-hidden="true">
          <span style={{ width: `${progress}%` }} />
        </div>
      </div>

      {actionError && <Banner kind="error">{actionError}</Banner>}

      {isEditing ? (
        <Editor
          key={current.outreach_id}
          initialSubject={current.subject ?? ''}
          initialBody={current.body ?? ''}
          errors={editErrors}
          saving={edit.isPending}
          onSave={saveEdit}
          onCancel={closeEditor}
        />
      ) : (
        <>
          <div className="review-layout">
            <DraftPane item={current} />
            <SourcePane
              item={current}
              criticDismissed={dismissedIds.has(current.outreach_id)}
              onDismissCritic={dismissCritic}
            />
          </div>
          <ActionBar
            onApprove={handleApprove}
            onEdit={openEditor}
            onRedraft={handleRedraft}
            onReject={handleReject}
            onSkip={handleSkip}
            disabled={busy}
          />
        </>
      )}
    </div>
  )
}
