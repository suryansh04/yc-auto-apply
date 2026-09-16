// Task 17: the Manual email screen - the NEEDS_MANUAL_EMAIL founders
// (backend/app/api/routers/manual.py's `GET /api/manual`, 25 on the live
// database). Founder, company, domain, LinkedIn link, the candidates
// already tried with their verdicts, the published-email hint when
// `resolve` found one, a text field for a hand-typed address, and a
// destructive-ish "give up" control (requires confirmation) that moves
// the founder to UNRESOLVED.
import { useState } from 'react'
import { useManual, useSetEmail } from '../api/hooks'
import DataTable from '../components/DataTable'
import Banner from '../components/Banner'

// V2 (T18 fix wave 2): a founder with several guessed-and-rejected
// candidates (7, for the Orangewood founders) rendered as an
// always-open bulleted list made every row ~270px tall, pushing the one
// actionable column ("Set email") far enough right that it clipped
// inside `.table-scroll`. Collapsed behind <details> by default, the
// row is one line ("N tried") unless the reviewer actually wants to
// see what was already attempted.
function CandidateList({ candidates }) {
  if (!candidates || candidates.length === 0) {
    return <span className="no-candidates">none tried yet</span>
  }
  return (
    <details className="candidate-list">
      <summary>{candidates.length} tried</summary>
      <ul>
        {candidates.map((candidate) => (
          <li key={candidate.address}>
            {candidate.address} &mdash; {candidate.verdict ?? 'unknown'}
          </li>
        ))}
      </ul>
    </details>
  )
}

// One row's hand-typed-address form + give-up control. Local state
// (`address`) is never cleared on a failed submit (a 422 from the
// validator) - the operator's typed text must survive exactly as they
// left it, per the brief.
function ManualEmailForm({ item }) {
  const [address, setAddress] = useState('')
  const [error, setError] = useState(null)
  const setEmail = useSetEmail()

  function submit(event) {
    event.preventDefault()
    setError(null)
    setEmail.mutate(
      { id: item.outreach_id, address },
      { onError: (err) => setError(err.errors ? err.errors.join(', ') : err.message) },
    )
  }

  function giveUp() {
    // Destructive-ish (moves the founder to UNRESOLVED, out of the
    // active pipeline) - requires an explicit confirm before firing.
    const ok = window.confirm(
      `Give up on ${item.founder_name}? This marks the founder UNRESOLVED.`,
    )
    if (!ok) return
    setError(null)
    setEmail.mutate({ id: item.outreach_id, giveUp: true })
  }

  return (
    <form className="manual-email-form" onSubmit={submit}>
      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
      <label>
        {`Address for ${item.founder_name}`}
        <input
          type="text"
          aria-label={`Address for ${item.founder_name}`}
          value={address}
          onChange={(event) => setAddress(event.target.value)}
        />
      </label>
      <button type="submit" disabled={setEmail.isPending}>
        Submit
      </button>
      <button type="button" onClick={giveUp} disabled={setEmail.isPending}>
        Give up
      </button>
    </form>
  )
}

const columns = [
  { key: 'founder_name', label: 'Founder', accessor: (row) => row.founder_name },
  { key: 'company_name', label: 'Company', accessor: (row) => row.company_name ?? '' },
  { key: 'domain', label: 'Domain', accessor: (row) => row.domain ?? '' },
  {
    key: 'linkedin',
    label: 'LinkedIn',
    sortable: false,
    render: (row) =>
      row.linkedin_url ? (
        <a href={row.linkedin_url} target="_blank" rel="noreferrer">
          LinkedIn
        </a>
      ) : (
        <span className="no-linkedin">&mdash;</span>
      ),
  },
  {
    key: 'candidates',
    label: 'Candidates tried',
    sortable: false,
    render: (row) => <CandidateList candidates={row.candidates} />,
  },
  {
    key: 'published_hint',
    label: 'Published hint',
    sortable: false,
    render: (row) =>
      row.published_hint ? (
        <span className="published-hint">{row.published_hint}</span>
      ) : (
        <span className="no-hint">&mdash;</span>
      ),
  },
  {
    key: 'set_email',
    label: 'Set email',
    sortable: false,
    render: (row) => <ManualEmailForm item={row} />,
  },
]

export default function ManualEmail() {
  const manualQuery = useManual()
  const items = manualQuery.data ?? []

  if (manualQuery.isLoading) return <p>Loading manual email queue...</p>

  if (manualQuery.isError) {
    return <Banner kind="error">Could not load manual queue: {manualQuery.error.message}</Banner>
  }

  if (items.length === 0) {
    return (
      <div className="manual-email">
        <h1>Manual email</h1>
        <p className="empty-state">Nothing needs a manual email right now.</p>
      </div>
    )
  }

  return (
    <div className="manual-email">
      <h1>Manual email</h1>
      <DataTable
        columns={columns}
        rows={items}
        getRowKey={(row) => row.outreach_id}
        defaultSortKey="founder_name"
      />
    </div>
  )
}
