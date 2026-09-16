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

it('renders exactly the seven registered job kinds as stage buttons, in order', async () => {
  // Mirrors the backend's own registry test
  // (test_job_kinds_has_exactly_the_seven_registered_stages,
  // backend/tests/api/test_jobs_stages.py) - there's no endpoint to
  // derive the stage list from, so this test IS the contract. Fix round
  // 1: Task 15 shipped with only 4 of the (then still 4) registered
  // kinds; 15a registered 3 more on the backend with no frontend test
  // pinning the full set, so the gap went uncaught. This closes it.
  mockStats({})
  mockJobs([])
  render(<Pipeline />)
  await screen.findByText('Pipeline')

  const buttons = screen.getAllByRole('button')
  expect(buttons.map((b) => b.textContent)).toEqual([
    'Sync',
    'Scrape',
    'Resolve',
    'Draft',
    'Poll replies',
    'Classify replies',
    'Schedule follow-ups',
  ])
})

it("describes classify-replies' machine-reply rule, not a nonexistent label", async () => {
  // The stage list test above pins labels only. This pins the
  // description: there is no "not interested" label (the real set is
  // INTERESTED/REJECTED/REFERRAL/AUTO_REPLY/OUT_OF_OFFICE/UNCLEAR), and
  // the single most important rule this stage implements - machine
  // replies never end a follow-up sequence - has to actually be stated.
  mockStats({})
  mockJobs([])
  render(<Pipeline />)
  await screen.findByText('Pipeline')

  const description = screen.getByText(/machine replies.*never end a follow-up sequence/i)
  expect(description).toBeInTheDocument()
  expect(description.textContent).not.toMatch(/not interested/i)
  expect(description.textContent).toMatch(/AUTO_REPLY/)
  expect(description.textContent).toMatch(/OUT_OF_OFFICE/)
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

// --- Final fix wave (F2, spec §9): the "re-run gmail-auth" banner ---------
//
// The backend records only the exception's TYPE NAME on a failed job; the
// banner keys on exactly `error === 'GmailAuthRequired'` for exactly the
// latest `poll-replies` job. The three negative tests below each pin one
// way the condition could be loosened (any failure / any kind / any
// historical job) and fail if it is.

const POLL_AUTH_FAILED = {
  id: 20, kind: 'poll-replies', status: 'failed', done: 0, total: null, error: 'GmailAuthRequired',
}

it('shows a re-run gmail-auth banner when the latest poll-replies job failed with GmailAuthRequired', async () => {
  mockStats({})
  mockJobs([POLL_AUTH_FAILED])
  render(<Pipeline />)
  const banner = await screen.findByRole('alert')
  expect(banner).toHaveTextContent(/Gmail authorisation expired/)
  expect(banner).toHaveTextContent(/gmail-auth/)
})

it('does not show the gmail-auth banner for a poll-replies job that failed for another reason', async () => {
  mockStats({})
  mockJobs([{ ...POLL_AUTH_FAILED, error: 'RuntimeError' }])
  render(<Pipeline />)
  // The ordinary failed cell still renders...
  expect(await screen.findByText(/failed: RuntimeError/)).toBeInTheDocument()
  // ...but nothing tells the operator to re-auth.
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByText(/gmail-auth/)).not.toBeInTheDocument()
})

it('does not show the gmail-auth banner when a NON-poll job carries that error name', async () => {
  mockStats({})
  mockJobs([{ ...POLL_AUTH_FAILED, kind: 'resolve' }])
  render(<Pipeline />)
  expect(await screen.findByText(/failed: GmailAuthRequired/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('clears the gmail-auth banner once a NEWER poll-replies job succeeded', async () => {
  mockStats({})
  mockJobs([
    POLL_AUTH_FAILED,
    { id: 21, kind: 'poll-replies', status: 'succeeded', done: 2, total: null, summary: 'fetched 2, stored 1, bounced 0, skipped 1' },
  ])
  render(<Pipeline />)
  expect(await screen.findByText(/fetched 2, stored 1/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('does not show the gmail-auth banner for a poll-replies job that is still running', async () => {
  mockStats({})
  mockJobs([{ ...POLL_AUTH_FAILED, status: 'running', error: 'GmailAuthRequired' }])
  render(<Pipeline />)
  // Wait for the jobs response to have rendered (the running cell), so
  // the absence of the banner is asserted against real data, not the
  // pre-fetch empty state.
  expect(await screen.findByText(/running/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
