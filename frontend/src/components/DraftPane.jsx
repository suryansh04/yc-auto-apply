// Task 16: the left-hand pane - the composed email exactly as it will be
// sent. Ruling R2: this renders `item.wire_text` (greeting + signature +
// opt-out line already applied), never the bare stored `item.body` - the
// operator reviews what actually goes out, not the raw draft.
//
// T13-1: `critic_verdict` is one of PASS / NEEDS_REVISION / ERROR (the
// critic itself crashed) - ERROR must never be shown as if it were a
// verdict, so it gets its own "critic unavailable" label instead of the
// raw code.
function criticLabel(verdict) {
  if (verdict === 'ERROR') return 'critic unavailable'
  if (verdict === 'PASS' || verdict === 'NEEDS_REVISION') return verdict
  return 'no verdict yet'
}

export default function DraftPane({ item, criticDismissed, onDismissCritic }) {
  if (!item) return null

  const showCriticNote = Boolean(item.critic_note) && !criticDismissed

  return (
    <section className="draft-pane" aria-label="Draft email">
      <h2>Draft</h2>

      {item.sequence_step > 0 && (
        <p className="sequence-step-badge">Follow-up (step {item.sequence_step})</p>
      )}

      {item.last_error && (
        <p className="needs-manual-write" role="alert">
          Needs manual write ({item.last_error}) — press [e] to edit before approving
        </p>
      )}

      <p className="critic-verdict">
        Critic: <span className={`critic-verdict-${item.critic_verdict ?? 'none'}`}>{criticLabel(item.critic_verdict)}</span>
      </p>

      {showCriticNote && (
        <p className="critic-note">
          {item.critic_note}
          <button type="button" onClick={onDismissCritic}>
            Dismiss
          </button>
        </p>
      )}

      <dl className="draft-meta">
        <dt>Subject</dt>
        <dd>{item.subject ?? '(no subject)'}</dd>
        <dt>Word count</dt>
        <dd>{item.word_count}</dd>
      </dl>

      <pre className="wire-text">{item.wire_text}</pre>
    </section>
  )
}
