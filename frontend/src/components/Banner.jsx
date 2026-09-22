// Generic inline banner for surfacing state the user must not miss - a
// failed job's error string, a start-job failure that wasn't the
// "already running" 409. `kind` picks styling and the a11y role: an error
// banner is `role="alert"` (announced immediately by a screen reader),
// anything else is `role="status"` (polite).
export default function Banner({ kind = 'info', children }) {
  return (
    <div className={`banner banner-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  )
}
