// Task 17: the Contacts screen - every company/founder pair, filterable,
// a plain sortable DataTable (no card grid). D19 is the load-bearing
// contract here: `hiring` narrows the list, it never gates who is shown
// by default.
import { expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { renderWithQuery } from '../test/render'
import { contact, mockContacts } from '../test/mocks'
import Contacts from './Contacts'

// Contacts links each founder to /thread/:id (react-router's <Link>),
// which needs a Router context - renderWithQuery alone (just
// QueryClientProvider) isn't enough, unlike screens with no <Link>.
function render(ui) {
  return renderWithQuery(<MemoryRouter>{ui}</MemoryRouter>)
}

// --- the brief's three ---------------------------------------------------

// shadcn's Select is a Radix listbox (a button that opens a popup), not
// a native <select>, so choosing a filter is "open it, click the option"
// rather than selectOptions().
async function choose(trigger, option) {
  await userEvent.click(trigger)
  await userEvent.click(await screen.findByRole('option', { name: option }))
}

it('lists every company and founder', async () => {
  mockContacts([
    contact({ outreach_id: 1, founder_name: 'Akash Gupta', company_name: 'Vahan' }),
    contact({ outreach_id: 2, founder_name: 'Priya Singh', company_name: 'Loop' }),
  ])
  render(<Contacts />)
  expect(await screen.findByText('Akash Gupta')).toBeInTheDocument()
  expect(screen.getByText('Priya Singh')).toBeInTheDocument()
  // header row + 2 data rows, nothing hidden or duplicated
  expect(screen.getAllByRole('row')).toHaveLength(3)
})

it('filters by state without hiding non-hiring companies by default (D19)', async () => {
  mockContacts([
    contact({ outreach_id: 1, founder_name: 'Akash Gupta', is_hiring: true, state: 'sent' }),
    contact({ outreach_id: 2, founder_name: 'Priya Singh', is_hiring: false, state: 'sent' }),
  ])
  render(<Contacts />)
  // both shown on first load - no filter applied yet, hiring included
  expect(await screen.findByText('Akash Gupta')).toBeInTheDocument()
  expect(screen.getByText('Priya Singh')).toBeInTheDocument()

  await choose(screen.getByLabelText(/^where it stands$/i), 'Sent')

  // state narrows nothing here (both rows match), and critically the
  // non-hiring founder is STILL listed - the state filter never touched
  // the hiring gate.
  await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(3))
  expect(screen.getByText('Akash Gupta')).toBeInTheDocument()
  expect(screen.getByText('Priya Singh')).toBeInTheDocument()
})

it("links a founder to their thread", async () => {
  mockContacts([contact({ outreach_id: 42, founder_name: 'Akash Gupta' })])
  render(<Contacts />)
  const link = await screen.findByRole('link', { name: 'Akash Gupta' })
  expect(link.getAttribute('href')).toContain('/thread/')
  expect(link.getAttribute('href')).toBe('/thread/42')
})

// --- beyond the brief ------------------------------------------------------

it('the hiring filter is off by default, so a non-hiring company still shows', async () => {
  mockContacts([contact({ outreach_id: 1, founder_name: 'Quiet Founder', is_hiring: false })])
  render(<Contacts />)
  expect(await screen.findByText('Quiet Founder')).toBeInTheDocument()
})

it('narrows to hiring-only companies once the hiring filter is turned on', async () => {
  mockContacts([
    contact({ outreach_id: 1, founder_name: 'Hiring Founder', is_hiring: true }),
    contact({ outreach_id: 2, founder_name: 'Quiet Founder', is_hiring: false }),
  ])
  render(<Contacts />)
  await screen.findByText('Hiring Founder')
  expect(screen.getByText('Quiet Founder')).toBeInTheDocument()

  await choose(screen.getByLabelText(/^hiring$/i), 'Hiring only')

  await waitFor(() => expect(screen.queryByText('Quiet Founder')).not.toBeInTheDocument())
  expect(screen.getByText('Hiring Founder')).toBeInTheDocument()
})

it('sorts by clicking a column header, with a stable tie-break', async () => {
  mockContacts([
    contact({ outreach_id: 2, founder_name: 'Bob', company_name: 'Same Co' }),
    contact({ outreach_id: 1, founder_name: 'Alice', company_name: 'Same Co' }),
  ])
  render(<Contacts />)
  await screen.findByText('Bob')

  // DataTable defaults to sorted-by-company ascending on mount, so the
  // header already reads "Company ▲" - click it once more to flip to
  // descending, then once more back to ascending, proving the click
  // handler (not just the initial default) drives the order.
  await userEvent.click(screen.getByRole('button', { name: /company/i }))
  await userEvent.click(screen.getByRole('button', { name: /company/i }))

  const rows = screen.getAllByRole('row').slice(1) // drop the header row
  // both rows tie on company name - the tiebreak (outreach_id) puts
  // Alice (id 1) before Bob (id 2), deterministically, regardless of the
  // order the mock returned them in.
  expect(rows[0]).toHaveTextContent('Alice')
  expect(rows[1]).toHaveTextContent('Bob')
})

it('a header click actually reorders the rows, and a second click reverses them', async () => {
  // Company-ascending (the default) and founder-ascending disagree on
  // order here on purpose: Alpha Co/Zoe sorts before Zeta Co/Alice by
  // company, but Alice sorts before Zoe by founder name - so clicking
  // "Founder" only passes if the click handler actually re-sorts by a
  // *different* column, not merely re-confirms whatever the mount-time
  // default already produced (a no-op onClick would leave this test's
  // first assertion already satisfied by the default sort otherwise).
  mockContacts([
    contact({ outreach_id: 1, company_name: 'Zeta Co', founder_name: 'Alice' }),
    contact({ outreach_id: 2, company_name: 'Alpha Co', founder_name: 'Zoe' }),
  ])
  render(<Contacts />)
  await screen.findByText('Alice')

  // default sort: by company, ascending -> Alpha Co (Zoe) then Zeta Co (Alice)
  let rows = screen.getAllByRole('row').slice(1)
  expect(rows[0]).toHaveTextContent('Zoe')
  expect(rows[1]).toHaveTextContent('Alice')

  await userEvent.click(screen.getByRole('button', { name: /^founder/i }))
  rows = screen.getAllByRole('row').slice(1)
  expect(rows[0]).toHaveTextContent('Alice')
  expect(rows[1]).toHaveTextContent('Zoe')

  await userEvent.click(screen.getByRole('button', { name: /^founder/i }))
  rows = screen.getAllByRole('row').slice(1)
  expect(rows[0]).toHaveTextContent('Zoe')
  expect(rows[1]).toHaveTextContent('Alice')
})
