// Task 17: the Contacts screen - every company/founder pair
// (backend/app/api/routers/contacts.py's `GET /api/contacts`), filterable
// by state/hiring/batch/email_status, as a plain dense DataTable (no card
// grid).
//
// D19 (restated in the router's own docstring): `hiring` is a VIEW
// control, never a gate - a non-hiring company is listed by default, same
// as every other row. The filter select below defaults to "" (no `hiring`
// param sent at all), so `useContacts({})`'s first request returns
// everyone; only selecting "Hiring only" narrows it.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useContacts } from '../api/hooks'
import DataTable from '../components/DataTable'
import Banner from '../components/Banner'

// OutreachState values (app/domain/states.py), mirrored by hand like
// Pipeline.jsx's FUNNEL_STATES - there's no endpoint to discover them at
// runtime.
const OUTREACH_STATES = [
  'new',
  'needs_email',
  'needs_manual_email',
  'ready_to_draft',
  'pending_approval',
  'approved',
  'sent',
  'replied',
  'closed',
  'discarded',
  'bounced',
  'unresolved',
  'failed',
]

const columns = [
  {
    key: 'company_name',
    label: 'Company',
    accessor: (row) => row.company_name ?? '',
  },
  {
    key: 'founder_name',
    label: 'Founder',
    accessor: (row) => row.founder_name,
    render: (row) => <Link to={`/thread/${row.outreach_id}`}>{row.founder_name}</Link>,
  },
  { key: 'domain', label: 'Domain', accessor: (row) => row.domain ?? '' },
  { key: 'state', label: 'State', accessor: (row) => row.state },
  { key: 'batch', label: 'Batch', accessor: (row) => row.batch ?? '' },
  { key: 'team_size', label: 'Team size', accessor: (row) => row.team_size ?? 0 },
  { key: 'is_hiring', label: 'Hiring', accessor: (row) => (row.is_hiring ? 'yes' : 'no') },
  { key: 'email_status', label: 'Email status', accessor: (row) => row.email_status },
]

export default function Contacts() {
  const [state, setState] = useState('')
  const [hiring, setHiring] = useState('')
  const [batch, setBatch] = useState('')
  const [emailStatus, setEmailStatus] = useState('')

  const filters = {}
  if (state !== '') filters.state = state
  if (hiring !== '') filters.hiring = hiring === 'true'
  if (batch !== '') filters.batch = batch
  if (emailStatus !== '') filters.email_status = emailStatus

  const contactsQuery = useContacts(filters)
  const contacts = contactsQuery.data ?? []

  return (
    <div className="contacts">
      <h1>Contacts</h1>

      <form className="contacts-filters" onSubmit={(event) => event.preventDefault()}>
        <label>
          State
          <select value={state} onChange={(event) => setState(event.target.value)}>
            <option value="">All</option>
            {OUTREACH_STATES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <label>
          Hiring
          <select value={hiring} onChange={(event) => setHiring(event.target.value)}>
            <option value="">All companies</option>
            <option value="true">Hiring only</option>
            <option value="false">Not hiring only</option>
          </select>
        </label>

        <label>
          Batch
          <input type="text" value={batch} onChange={(event) => setBatch(event.target.value)} />
        </label>

        <label>
          Email status
          <select value={emailStatus} onChange={(event) => setEmailStatus(event.target.value)}>
            <option value="">All</option>
            <option value="resolved">Resolved</option>
            <option value="unresolved">Unresolved</option>
          </select>
        </label>
      </form>

      {contactsQuery.isLoading && <p>Loading contacts...</p>}

      {contactsQuery.isError && (
        <Banner kind="error">Could not load contacts: {contactsQuery.error.message}</Banner>
      )}

      {!contactsQuery.isLoading && !contactsQuery.isError && (
        contacts.length === 0 ? (
          <p className="empty-state">No contacts match these filters.</p>
        ) : (
          <DataTable
            columns={columns}
            rows={contacts}
            getRowKey={(row) => row.outreach_id}
            defaultSortKey="company_name"
          />
        )
      )}
    </div>
  )
}
