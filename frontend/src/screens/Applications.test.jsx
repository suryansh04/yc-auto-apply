// Applications: every founder by what became of the letter.
//
// The Founders screen already filters by raw STATE. This asks a different
// question - `sent`, `replied`, `closed` and `bounced` are four states
// and one outcome, and `pending_approval` is one state and two outcomes
// depending on whether the operator passed over it.
import { expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { renderWithQuery } from '../test/render'
import { mockApplications } from '../test/mocks'
import Applications from './Applications'

const render = () => renderWithQuery(<MemoryRouter><Applications /></MemoryRouter>)

const app = (over = {}) => ({
  outreach_id: 1,
  company_name: 'Vahan',
  founder_name: 'Madhav Krishna',
  to: 'madhav@vahan.ai',
  state: 'pending_approval',
  outcome: 'waiting',
  subject: 'Backend intern at Vahan',
  sent_at: null,
  skipped_at: null,
  last_error: null,
  ...over,
})

const FIXTURE = [
  app({ outreach_id: 1, outcome: 'sent', state: 'sent', sent_at: '2026-09-22T10:00:00Z' }),
  app({ outreach_id: 2, outcome: 'sent', state: 'replied', company_name: 'Lokal',
        founder_name: 'Jani Pasha', sent_at: '2026-09-21T10:00:00Z' }),
  app({ outreach_id: 3, outcome: 'rejected', state: 'discarded', founder_name: 'Rej Ected' }),
  app({ outreach_id: 4, outcome: 'skipped', state: 'pending_approval',
        founder_name: 'Ski Pped', skipped_at: '2026-09-22T09:00:00Z' }),
  app({ outreach_id: 5, outcome: 'waiting', founder_name: 'Wai Ting' }),
  app({ outreach_id: 6, outcome: 'not_started', state: 'new', founder_name: 'Not Started',
        subject: null }),
]

it('lists every application with a count per outcome', async () => {
  mockApplications(FIXTURE)
  render()
  expect(await screen.findByRole('button', { name: /^All\s*6$/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /^Sent\s*2$/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /^Rejected\s*1$/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /^Skipped\s*1$/ })).toBeInTheDocument()
})

it('filters to one outcome', async () => {
  mockApplications(FIXTURE)
  render()
  await userEvent.click(await screen.findByRole('button', { name: /^Rejected\s*1$/ }))
  expect(screen.getByText('Rej Ected')).toBeInTheDocument()
  expect(screen.queryByText('Wai Ting')).not.toBeInTheDocument()
})

it('counts a replied letter as sent', async () => {
  // Four states, one outcome: it left the building.
  mockApplications(FIXTURE)
  render()
  await userEvent.click(await screen.findByRole('button', { name: /^Sent\s*2$/ }))
  expect(screen.getByText('Jani Pasha')).toBeInTheDocument()
  // ...and the raw state still travels with it, so the bucket hides nothing.
  expect(screen.getByText('They replied')).toBeInTheDocument()
})

it('tells a skipped letter apart from one not yet reached', async () => {
  // The whole reason skipped_at exists: both are pending_approval.
  mockApplications(FIXTURE)
  render()
  await userEvent.click(await screen.findByRole('button', { name: /^Skipped\s*1$/ }))
  expect(screen.getByText('Ski Pped')).toBeInTheDocument()
  expect(screen.queryByText('Wai Ting')).not.toBeInTheDocument()
})

it('says what each filter means', async () => {
  mockApplications(FIXTURE)
  render()
  await userEvent.click(await screen.findByRole('button', { name: /^Rejected\s*1$/ }))
  expect(screen.getByText(/nobody received these/i)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /^Skipped\s*1$/ }))
  expect(screen.getByText(/moved on without deciding/i)).toBeInTheDocument()
})

it('links a founder to their conversation', async () => {
  mockApplications([FIXTURE[0]])
  render()
  expect(await screen.findByRole('link', { name: 'Madhav Krishna' }))
    .toHaveAttribute('href', '/thread/1')
})

it('says so when a row has no draft yet', async () => {
  mockApplications([FIXTURE[5]])
  render()
  expect(await screen.findByText(/no draft yet/i)).toBeInTheDocument()
})

it('has something to say when a filter is empty', async () => {
  mockApplications([FIXTURE[4]])
  render()
  await userEvent.click(await screen.findByRole('button', { name: /^Sent\s*0$/ }))
  expect(screen.getByText(/nothing here yet/i)).toBeInTheDocument()
})


// --- paging (operator request 2026-09-22) ---------------------------------

it('renders a page at a time and says how many of the whole there are', async () => {
  // 284 rows is 58 KB over the wire and two thousand table cells in the
  // DOM. The list is still fetched whole; it is RENDERED incrementally.
  mockApplications(Array.from({ length: 120 }, (_, i) =>
    app({ outreach_id: i + 1, founder_name: `Founder ${i + 1}` })))
  render()
  expect(await screen.findByText('Showing 50 of 120')).toBeInTheDocument()
  expect(screen.getByText('Founder 50')).toBeInTheDocument()
  expect(screen.queryByText('Founder 51')).not.toBeInTheDocument()
})

it('loads another page on demand', async () => {
  mockApplications(Array.from({ length: 120 }, (_, i) =>
    app({ outreach_id: i + 1, founder_name: `Founder ${i + 1}` })))
  render()
  await userEvent.click(await screen.findByRole('button', { name: /load more/i }))
  expect(screen.getByText('Showing 100 of 120')).toBeInTheDocument()
  expect(screen.getByText('Founder 51')).toBeInTheDocument()
})

it('offers no load-more once everything is on screen', async () => {
  mockApplications([app()])
  render()
  expect(await screen.findByText('Showing 1 of 1')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
})

it('goes back to the first page when the filter changes', async () => {
  const many = Array.from({ length: 120 }, (_, i) =>
    app({ outreach_id: i + 1, founder_name: `Founder ${i + 1}`, outcome: 'waiting' }))
  mockApplications([...many, app({ outreach_id: 999, outcome: 'sent', state: 'sent' })])
  render()
  await userEvent.click(await screen.findByRole('button', { name: /load more/i }))
  expect(screen.getByText('Showing 100 of 121')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /^Waiting on you\s*120$/ }))
  // Not 100 rows deep in a list the operator has not seen.
  expect(screen.getByText('Showing 50 of 120')).toBeInTheDocument()
})
