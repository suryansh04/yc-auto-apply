// Task 16 (+ fix round 1, T16-3/F6): single-key shortcuts for the Review
// Queue (a/e/d/r/s/j/k), kept in its own hook so the one thing that must
// never regress silently - the input-focus guard - has a dedicated,
// isolated test file (useKeyboard.test.jsx) rather than only being
// exercised indirectly through ReviewQueue.test.jsx.
import { useEffect, useLayoutEffect, useRef } from 'react'

const EDITABLE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

function isEditableTarget(target) {
  if (!target) return false
  if (EDITABLE_TAGS.has(target.tagName)) return true
  if (target.isContentEditable) return true
  // jsdom (the test environment) does not implement the `isContentEditable`
  // IDL getter, so fall back to reading the attribute directly - a real
  // browser's `isContentEditable` already covers this, this is purely for
  // the guard to be testable under jsdom.
  const attr = typeof target.getAttribute === 'function' ? target.getAttribute('contenteditable') : null
  if (attr === '' || attr === 'true' || attr === 'plaintext-only') return true
  return false
}

// A binding is either a plain handler function (the common case - guarded
// by focus, like every review-queue action key) or `{ handler,
// bypassFocusGuard: true }` for the rare key that must fire even while a
// text field has focus - Escape-to-cancel-the-editor being the only one
// today, since typing Escape while still inside the body textarea is the
// normal way an operator backs out of an edit.
function normalizeBinding(entry) {
  if (typeof entry === 'function') return { handler: entry, bypassFocusGuard: false }
  return { handler: entry.handler, bypassFocusGuard: Boolean(entry.bypassFocusGuard) }
}

/**
 * Binds single-key shortcuts on `window`'s keydown. By default, a
 * binding is inert while focus is inside a text input / textarea /
 * select / contentEditable element - a stray "a" while the operator is
 * typing an edited draft must never approve it. Pass `{ handler,
 * bypassFocusGuard: true }` instead of a bare function to opt a specific
 * key out of that guard.
 *
 * `bindings` is a plain `{ key: handler | { handler, bypassFocusGuard } }`
 * map, matched against `KeyboardEvent.key` exactly as given (so pass
 * lowercase letters for letter shortcuts, `'Escape'` for Escape). It may
 * be a fresh object every render - only its latest value is read (via a
 * ref kept in sync by its own layout effect, never written during
 * render), so passing a new object each render never re-registers the
 * DOM listener.
 *
 * `{ enabled: false }` suspends every binding outright (e.g. all of the
 * review-queue's action keys while a modal editor is open) - a second
 * line of defence on top of the focus guard, not a replacement for it.
 */
export function useKeyboard(bindings, { enabled = true } = {}) {
  const bindingsRef = useRef(bindings)

  // Keep the ref in sync with the latest `bindings` without writing to it
  // during render (a lint violation, and a correctness smell - a ref
  // write should never be able to race a concurrent render). Fix round
  // 2: `useLayoutEffect` rather than `useEffect` - both happen to work
  // today (React flushes passive effects synchronously for the sync-lane
  // commits this screen's updates are), but the layout effect removes
  // the dependence on that scheduling detail entirely, at zero cost,
  // since a keydown can never fire before the browser has painted.
  useLayoutEffect(() => {
    bindingsRef.current = bindings
  })

  useEffect(() => {
    if (!enabled) return undefined

    function onKeyDown(event) {
      const raw = bindingsRef.current?.[event.key]
      if (!raw) return
      const { handler, bypassFocusGuard } = normalizeBinding(raw)
      if (!bypassFocusGuard && isEditableTarget(event.target)) return
      event.preventDefault()
      handler(event)
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [enabled])
}
