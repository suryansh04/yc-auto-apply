// One "start this stage" button. All state (whether the kind is already
// running, whether a start is in flight) is computed by the caller
// (Pipeline.jsx) - this component just renders it, so it stays reusable
// for any future screen that starts a job kind.
export default function JobButton({ kind, label, disabled, onClick }) {
  return (
    <button type="button" className="job-button" disabled={disabled} onClick={onClick}>
      {label ?? kind}
    </button>
  )
}
