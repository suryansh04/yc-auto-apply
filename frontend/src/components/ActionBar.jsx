// The decision rail. Visible mouse equivalents of the review-queue
// keyboard shortcuts (fix round 1, T16-1): each button dispatches
// through the exact same handler its key does, so a click and its
// keypress are indistinguishable to the mutation underneath. Disabled
// while a mutation is in flight (T16-4/F5) - the same condition that
// makes the keys ignore a second press.
//
// Pinned to the bottom of the viewport (styles/review-queue.css): a
// 250-word letter used to push these off screen, so the operator had to
// scroll back up to act on what they had just finished reading.
export default function ActionBar({ onApprove, onEdit, onRedraft, onReject, onSkip, disabled }) {
  return (
    <div className="action-bar" role="group" aria-label="Review actions">
      <button type="button" className="primary" onClick={onApprove} disabled={disabled}>
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
