// Task 16: single-key shortcuts for the Review Queue (a/e/d/r/s/j/k), kept
// in its own hook so the one thing that must never regress silently - the
// input-focus guard - has a dedicated, isolated test file
// (useKeyboard.test.jsx) rather than only being exercised indirectly
// through ReviewQueue.test.jsx.
import { useEffect, useRef } from 'react'

const EDITABLE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

function isEditableTarget(target) {
  if (!target) return false
  if (EDITABLE_TAGS.has(target.tagName)) return true
  if (target.isContentEditable) return true
  return false
}

/**
 * Binds single-key shortcuts on `window`'s keydown, as long as focus is
 * not inside a text input / textarea / select / contentEditable element -
 * a stray "a" while the operator is typing an edited draft must never
 * approve it.
 *
 * `bindings` is a plain `{ key: handler }` map, matched against
 * `KeyboardEvent.key` exactly as given (so pass lowercase letters for
 * letter shortcuts). It may be a fresh object every render - only its
 * latest value is read (via a ref), so passing a new object each render
 * never re-registers the DOM listener.
 *
 * `{ enabled: false }` suspends every binding outright (e.g. while a
 * modal editor is open) - a second line of defence on top of the focus
 * guard, not a replacement for it.
 */
export function useKeyboard(bindings, { enabled = true } = {}) {
  const bindingsRef = useRef(bindings)
  bindingsRef.current = bindings

  useEffect(() => {
    if (!enabled) return undefined

    function onKeyDown(event) {
      if (isEditableTarget(event.target)) return
      const handler = bindingsRef.current?.[event.key]
      if (!handler) return
      event.preventDefault()
      handler(event)
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [enabled])
}
