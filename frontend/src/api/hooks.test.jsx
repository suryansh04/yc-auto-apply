// End-to-end test of one mutation hook through renderWithQuery + MSW: a
// component calls useApprove() and, on success, both the queue and stats
// queries it invalidates are refetched (proven by a second GET landing at
// the mock server, and the on-screen queue count updating).
import { expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { waitFor, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { server } from '../test/server'
import { renderWithQuery } from '../test/render'
import { useApprove, useQueue, useStats } from './hooks'

// server's listen/resetHandlers/close lifecycle is owned globally by
// src/test/setup.js - this file only registers handlers via server.use().
let queueGetCount = 0
let statsGetCount = 0

function ApproveHarness() {
  const { data: queue } = useQueue()
  const { data: stats } = useStats()
  const approve = useApprove()
  return (
    <div>
      <div data-testid="queue-len">{queue ? queue.length : 'loading'}</div>
      <div data-testid="stats-quota">{stats ? stats.quota.provider : 'loading'}</div>
      <button onClick={() => approve.mutate(1)}>approve</button>
    </div>
  )
}

it('useApprove invalidates queue and stats after a successful approve', async () => {
  server.use(
    http.get('/api/queue', () => {
      queueGetCount += 1
      // Second GET (after invalidation) returns a shorter list, proving
      // a refetch actually happened rather than the cache just going stale.
      return HttpResponse.json(queueGetCount === 1 ? [{ outreach_id: 1 }, { outreach_id: 2 }] : [{ outreach_id: 2 }])
    }),
    http.get('/api/stats', () => {
      statsGetCount += 1
      return HttpResponse.json({ counts: {}, quota: { provider: 'email-verifier', configured: false, daily_limit: 100 }, jobs: [] })
    }),
    http.post('/api/outreach/1/approve', () => HttpResponse.json({ ok: true })),
  )

  renderWithQuery(<ApproveHarness />)

  await waitFor(() => expect(screen.getByTestId('queue-len')).toHaveTextContent('2'))

  await userEvent.click(screen.getByText('approve'))

  await waitFor(() => expect(screen.getByTestId('queue-len')).toHaveTextContent('1'))
  expect(queueGetCount).toBe(2)
  // stats was invalidated too - refetched at least a second time.
  expect(statsGetCount).toBeGreaterThanOrEqual(2)
})
