// Task 16: the Review Queue - the screen this whole project exists for.
// Every outreach email is drafted by an LLM, validated deterministically,
// and MUST be approved by a human before it can be sent; this is where
// that gate happens, keyboard-driven, for ~270 drafts in a row.
//
// `useApprove`/`useEdit`/`useReject`/`useRedraft` (Task 14) each
// invalidate `['queue']` + `['stats']` (or `['queue']` + `['queue', id]`
// for edit) on success, but nothing about the *screen's own* navigation -
// the optimistic "move to the next item immediately, roll back and show
// an error if the mutation fails" is this file's job, not the hooks'.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useApprove, useEdit, useQueue, useRedraft, useReject } from '../api/hooks'
import { useKeyboard } from '../hooks/useKeyboard'
import DraftPane from '../components/DraftPane'
import SourcePane from '../components/SourcePane'
import Editor from '../components/Editor'
import Banner from '../components/Banner'

export default function ReviewQueue() {
  const queryClient = useQueryClient()
  const queueQuery = useQueue()
  const approve = useApprove()
  const edit = useEdit()
  const reject = useReject()
  const redraft = useRedraft()

  const items = queueQuery.data ?? []

  const [index, setIndex] = useState(0)
  const [isEditing, setIsEditing] = useState(false)
  const [editErrors, setEditErrors] = useState([])
  const [actionError, setActionError] = useState(null)
  const [dismissedIds, setDismissedIds] = useState(() => new Set())

  const clampedIndex = items.length === 0 ? 0 : Math.min(index, items.length - 1)
  const current = items[clampedIndex]

  // Keyboard handlers are bound once (see useKeyboard below) and read via
  // a ref, so a handler invoked synchronously off a DOM keydown can run
  // against a React render that is one tick behind - TanStack Query's
  // cache can already hold the freshly-fetched queue while the component
  // itself hasn't re-rendered with it yet. Reading straight from the
  // query cache here (rather than the `items`/`current` closed over from
  // the last completed render) means a key pressed the instant the queue
  // finishes loading still resolves against the real data, not an empty
  // "still loading" snapshot.
  function freshItems() {
    return queryClient.getQueryData(['queue']) ?? []
  }

  function withCurrent(callback) {
    const list = freshItems()
    if (list.length === 0) return
    const actedIndex = Math.min(index, list.length - 1)
    callback(list[actedIndex], actedIndex, list)
  }

  function goNext() {
    const length = freshItems().length
    setIndex((i) => Math.min(i + 1, Math.max(length - 1, 0)))
  }

  function goPrev() {
    setIndex((i) => Math.max(i - 1, 0))
  }

  // Shared shape for approve/reject/redraft: advance immediately (so the
  // operator never waits on the network mid-review), roll back to the
  // item that was actually acted on and surface the failure if the
  // mutation turns out to have failed (e.g. a 409 on a flagged draft).
  function runAction(mutation, verb) {
    withCurrent((currentItem, actedIndex) => {
      setActionError(null)
      setIndex(actedIndex + 1)
      mutation.mutate(currentItem.outreach_id, {
        onError: (err) => {
          setIndex(actedIndex)
          setActionError(`Could not ${verb}: ${err.message}`)
        },
      })
    })
  }

  function handleApprove() {
    runAction(approve, 'approve')
  }

  function handleReject() {
    runAction(reject, 'reject')
  }

  function handleRedraft() {
    runAction(redraft, 'redraft')
  }

  function handleSkip() {
    setActionError(null)
    goNext()
  }

  function openEditor() {
    withCurrent(() => {
      setEditErrors([])
      setActionError(null)
      setIsEditing(true)
    })
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

  // Always registered (never conditionally skipped) so the input-focus
  // guard inside useKeyboard is the thing actually keeping "a" from
  // approving mid-edit, not a redundant "isEditing" flag here that could
  // mask a regression in the guard itself.
  useKeyboard({
    a: handleApprove,
    e: openEditor,
    d: handleRedraft,
    r: handleReject,
    s: handleSkip,
    j: goNext,
    k: goPrev,
  })

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

  return (
    <div className="review-queue">
      <h1>Review Queue</h1>
      <p className="queue-counter">
        {clampedIndex + 1} of {items.length}
      </p>

      {actionError && <Banner kind="error">{actionError}</Banner>}

      {isEditing ? (
        <Editor
          initialSubject={current.subject ?? ''}
          initialBody={current.body ?? ''}
          errors={editErrors}
          onSave={saveEdit}
          onCancel={closeEditor}
        />
      ) : (
        <div className="review-panes">
          <DraftPane
            item={current}
            criticDismissed={dismissedIds.has(current.outreach_id)}
            onDismissCritic={dismissCritic}
          />
          <SourcePane item={current} />
        </div>
      )}
    </div>
  )
}
