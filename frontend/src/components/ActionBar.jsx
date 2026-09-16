// Fix round 1, T16-1: visible mouse equivalents of the review-queue
// keyboard shortcuts. Each button dispatches through the exact same
// handler its key does (wired by ReviewQueue.jsx), so a click and its
// keypress are indistinguishable to the mutation underneath. Disabled
// while a mutation is already in flight (T16-4/F5's in-flight lock) - the
// same condition that makes the keys themselves ignore a second press.
export default function ActionBar({ onApprove, onEdit, onRedraft, onReject, onSkip, disabled }) {
  return (
    <div className="action-bar" role="group" aria-label="Review actions">
      <button type="button" onClick={onApprove} disabled={disabled}>
        Approve <kbd>a</kbd>
      </button>
      <button type="button" onClick={onEdit} disabled={disabled}>
        Edit <kbd>e</kbd>
      </button>
      <button type="button" onClick={onRedraft} disabled={disabled}>
        Redraft <kbd>d</kbd>
      </button>
      <button type="button" onClick={onReject} disabled={disabled}>
        Reject <kbd>r</kbd>
      </button>
      <button type="button" onClick={onSkip} disabled={disabled}>
        Skip <kbd>s</kbd>
      </button>
    </div>
  )
}
