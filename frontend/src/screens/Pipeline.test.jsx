// Task 15's Pipeline screen tests. server's listen/resetHandlers/close
// lifecycle is owned globally by src/test/setup.js - this file only
// registers handlers via the src/test/mocks.js helpers (server.use()
// under the hood).
import { expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithQuery as render } from '../test/render'
import { mockJobs, mockStartJob, mockStats } from '../test/mocks'
import Pipeline from './Pipeline'

it('shows the funnel counts by state', async () => {
  mockStats({ counts: { new: 249, needs_manual_email: 20, sent: 3 } })
  mockJobs([])
  render(<Pipeline />)
  expect(await screen.findByText('249')).toBeInTheDocument()
})

it('starts a job when its button is clicked', async () => {
  mockStats({})
  mockJobs([])
  const started = mockStartJob('scrape')
  render(<Pipeline />)
  await userEvent.click(await screen.findByRole('button', { name: /scrape/i }))
  await waitFor(() => expect(started).toHaveBeenCalled())
})

it('shows live progress while a job runs', async () => {
  mockStats({})
  mockJobs([{ id: 1, kind: 'scrape', status: 'running', done: 30, total: 145 }])
  render(<Pipeline />)
  expect(await screen.findByText(/30\s*\/\s*145/)).toBeInTheDocument()
})

it('disables a kind that is already running', async () => {
  mockStats({})
  mockJobs([{ id: 1, kind: 'scrape', status: 'running', done: 1, total: 145 }])
  render(<Pipeline />)
  await screen.findByRole('button', { name: /scrape/i })
  await waitFor(() => expect(screen.getByRole('button', { name: /scrape/i })).toBeDisabled())
})

it('shows a failed job with its error instead of hiding it', async () => {
  mockStats({})
  mockJobs([{ id: 2, kind: 'resolve', status: 'failed', done: 0, total: null, error: 'VerifierError' }])
  render(<Pipeline />)
  expect(await screen.findByText(/VerifierError/)).toBeInTheDocument()
})

it('stops polling once no job is running', async () => {
  mockStats({})
  const spy = mockJobs([{ id: 3, kind: 'sync', status: 'succeeded', done: 5, total: 5 }])
  render(<Pipeline />)
  await screen.findByRole('button', { name: /sync/i })
  const callsAfterSettle = spy.mock.calls.length
  await new Promise((r) => setTimeout(r, 2500))
  expect(spy.mock.calls.length).toBe(callsAfterSettle)
})

// --- beyond the brief's six -------------------------------------------

it('a 409 from start disables the button and shows the in-flight job, not an error', async () => {
  mockStats({})
  // First GET (initial load) shows nothing running; after the 409, the
  // component refetches /api/jobs and this second response shows the
  // kind now running - proving the screen reconciles with reality rather
  // than displaying the raw 409 as an error banner.
  let call = 0
  const jobsSpy = vi.fn(() => {
    call += 1
    const body =
      call === 1
        ? []
        : [{ id: 9, kind: 'scrape', status: 'running', done: 0, total: null }]
    return new Response(JSON.stringify(body), {
      headers: { 'Content-Type': 'application/json' },
    })
  })
  const { server } = await import('../test/server')
  const { http } = await import('msw')
  server.use(http.get('/api/jobs', jobsSpy))
  mockStartJob('scrape', { conflict: true, jobId: 9 })

  render(<Pipeline />)
  const button = await screen.findByRole('button', { name: /scrape/i })
  expect(button).not.toBeDisabled()

  await userEvent.click(button)

  await waitFor(() => expect(screen.getByRole('button', { name: /scrape/i })).toBeDisabled())
  // No generic "could not start job" error banner should appear.
  expect(screen.queryByText(/could not start job/i)).not.toBeInTheDocument()
})

it('renders needs_manual_email as its own count, not folded into new', async () => {
  mockStats({ counts: { new: 100, needs_manual_email: 25 } })
  mockJobs([])
  render(<Pipeline />)
  expect(await screen.findByText('100')).toBeInTheDocument()
  expect(await screen.findByText('25')).toBeInTheDocument()
  expect(screen.getByText('Needs manual email')).toBeInTheDocument()
})

it('shows progress without a slash when total is unknown (null)', async () => {
  mockStats({})
  mockJobs([{ id: 4, kind: 'sync', status: 'running', done: 12, total: null }])
  render(<Pipeline />)
  const status = await screen.findByText((text) => text.includes('12'))
  expect(status.textContent).not.toMatch(/\//)
})

it('renders every funnel state even when its count is 0', async () => {
  mockStats({})
  mockJobs([])
  render(<Pipeline />)
  await screen.findByText('Pipeline')
  for (const label of [
    'New',
    'Needs email',
    'Needs manual email',
    'Ready to draft',
    'Pending approval',
    'Approved',
    'Sent',
    'Replied',
    'Closed',
    'Unresolved',
    'Discarded',
    'Bounced',
    'Failed',
  ]) {
    expect(screen.getByText(label)).toBeInTheDocument()
  }
  // Every one of those rows reads 0 - not blank, not missing.
  expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(13)
})

it('polls jobs at a 2000ms interval while running, not sooner', async () => {
  mockStats({})
  const spy = mockJobs([{ id: 5, kind: 'scrape', status: 'running', done: 1, total: 10 }])
  render(<Pipeline />)
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1))

  // Comfortably before the 2000ms mark: still just the initial fetch. A
  // shorter interval (e.g. react-query's own low default) would already
  // have fired a second call by here.
  await new Promise((r) => setTimeout(r, 1200))
  expect(spy).toHaveBeenCalledTimes(1)

  // Past the 2000ms mark: exactly one more call.
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(2), { timeout: 2000 })
}, 8000)
