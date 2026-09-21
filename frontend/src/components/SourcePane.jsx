// The docket: everything you need to judge the letter without leaving
// it. Who it goes to, the one research row it cites, and what the checks
// said. Sticky beside the letter on a wide screen (styles/review-queue),
// so the evidence stays put while you read the claim.

// The source kinds are database enum values (`long_description`,
// `job_posting`). Nobody reviewing a letter thinks in those terms, so
// say where the fact came from in the words a person would use.
const SOURCE_KINDS = {
  news: 'From a news story about them',
  launch: 'From their launch post',
  job_posting: 'From a job they posted',
  long_description: 'From their own description of the company',
}

// T13-1: `critic_verdict` is PASS / NEEDS_REVISION / ERROR (the critic
// itself crashed). ERROR must never read as a verdict on the letter.
function criticCheck(verdict) {
  if (verdict === 'PASS') return { tone: 'ok', label: 'Second opinion: reads well' }
  if (verdict === 'NEEDS_REVISION') return { tone: 'warn', label: 'Second opinion: worth another look' }
  if (verdict === 'ERROR') return { tone: '', label: 'Second opinion unavailable' }
  return { tone: '', label: 'No second opinion yet' }
}

export default function SourcePane({ item, criticDismissed, onDismissCritic }) {
  if (!item) return null

  const critic = criticCheck(item.critic_verdict)
  const showCriticNote = Boolean(item.critic_note) && !criticDismissed

  return (
    <aside className="source-pane" aria-label="Company, source and checks">
      <div>
        <h2>{item.company_name ?? 'Unknown company'}</h2>
        {item.company_one_liner && <p className="company-one-liner">{item.company_one_liner}</p>}
      </div>

      <dl className="founder-details">
        <dt>Founder</dt>
        <dd>
          {item.founder_name}
          {item.founder_title ? ` — ${item.founder_title}` : ''}
        </dd>
      </dl>

      <div className="cited-source">
        <h3>What the letter is built on</h3>
        {item.source_text ? (
          <>
            <p className="source-kind">{SOURCE_KINDS[item.source_kind] ?? 'From their research material'}</p>
            <p className="source-text">{item.source_text}</p>
            {item.source_publication && (
              <p className="source-publication">{item.source_publication}</p>
            )}
          </>
        ) : (
          <p className="source-text source-text-none">
            Nothing cited — this one was written by hand.
          </p>
        )}
      </div>

      <div className="checks">
        {item.last_error && (
          <p className="mark mark-warn" role="alert">
            Needs a fix before approving: {item.last_error}
          </p>
        )}

        <p className={critic.tone ? `mark mark-${critic.tone}` : 'mark'}>{critic.label}</p>

        {showCriticNote && (
          <p className="critic-note">
            {item.critic_note}
            <button type="button" onClick={onDismissCritic}>
              Dismiss
            </button>
          </p>
        )}
      </div>
    </aside>
  )
}
