// Task 16 (+ fix round 1, coordinator ruling T16-2): the Review Queue -
// the screen this whole project exists for. Every outreach email is
// drafted by an LLM, validated deterministically, and MUST be approved
// by a human before it can be sent; this is where that gate happens,
// keyboard- and mouse-driven, for ~270 drafts in a row.
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
// eventually returns.
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

  // Resolve the displayed item by id, derived during render rather than
  // synced via an effect (an effect that calls setState on every `items`
  // change just to "fix up" a derived value is a cascading-render smell,
  // and unnecessary here): if `currentId` matches something in the
  // current list, show that; otherwise (nothing selected yet on first
  // load, or the selected id fell out of the list for a reason other
  // than this screen's own action - e.g. another reviewer approved it
  // first) fall back to the first item. The common case (this screen's
  // own approve/reject/redraft) already set `currentId` to the right
  // next id *before* the mutation fired, so by the time a refetch lands
  // that id is still present and this falls through to the first branch.
  const current =
    items.find((it) => it.outreach_id === currentId) ?? (items.length > 0 ? items[0] : null)
  const position = current ? items.findIndex((it) => it.outreach_id === current.outreach_id) + 1 : 0

  // T16-4/F5: one in-flight approve/reject/redraft at a time - "aa" must
  // never double-submit the same action. Keyed on the mutations
  // themselves rather than per-id since only one item can be acted on at
  // once from this screen anyway.
  const busy = approve.isPending || reject.isPending || redraft.isPending

  function goNext() {
    if (!current) return
    const idx = items.findIndex((it) => it.outreach_id === current.outreach_id)
    const next = items[idx + 1]
    if (next) setCurrentId(next.outreach_id)
  }

  function goPrev() {
    if (!current) return
    const idx = items.findIndex((it) => it.outreach_id === current.outreach_id)
    const prev = items[idx - 1]
    if (prev) setCurrentId(prev.outreach_id)
  }

  // Shared shape for approve/reject/redraft: advance immediately (so the
  // operator never waits on the network mid-review), to the id that was
  // next to the acted-on item in *this* render's list (F1) - roll back to
  // the exact id that was acted on and surface the failure if the
  // mutation turns out to have failed (F4).
  function actOn(mutation, verb) {
    if (!current || busy) return
    const actedId = current.outreach_id
    const nextId = idAfter(items, actedId)
    setActionError(null)
    setCurrentId(nextId)
    mutation.mutate(actedId, {
      onError: (err) => {
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

  // `current` always resolves to a real item once `items` is non-empty
  // (see its derivation above) - this is just a defensive fallback, never
  // expected to render.
  if (!current) return <p>Loading queue...</p>

  return (
    <div className="review-queue">
      <h1>Review Queue</h1>
      <p className="queue-counter">
        {position} of {items.length}
      </p>

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
          <div className="review-panes">
            <DraftPane
              item={current}
              criticDismissed={dismissedIds.has(current.outreach_id)}
              onDismissCritic={dismissCritic}
            />
            <SourcePane item={current} />
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
