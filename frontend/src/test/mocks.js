// Small, reusable MSW mock helpers for screen tests (Task 15's Pipeline,
// and Tasks 16-17's screens after it). Each helper calls the shared
// `server.use(...)` (src/test/server.js) and returns a vi.fn() spy so a
// test can assert it was called, or count calls (e.g. to prove polling
// starts/stops) without re-deriving the handler wiring every time.
import { vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from './server'

// Every OutreachState value (app/domain/states.py) - kept in sync by hand
// since the frontend has no generated types. GET /api/stats always returns
// every state, zeros included (T3-1), so mockStats does the same rather
// than letting a test's partial `counts` silently produce a sparse object
// a naive component could mistake for "no data yet".
export const ALL_OUTREACH_STATES = [
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

const DEFAULT_QUOTA = { provider: 'email-verifier', configured: false, daily_limit: 100 }

/**
 * Mocks GET /api/stats. `counts` may be partial - every other
 * OutreachState is filled in at 0, matching the real endpoint's contract.
 * `quota` and `jobs` default to a not-configured quota and an empty job
 * list. Returns the response body's spy (vi.fn) wrapping the handler, so a
 * test can assert call counts if it cares.
 */
export function mockStats({ counts = {}, quota = DEFAULT_QUOTA, jobs = [] } = {}) {
  const fullCounts = Object.fromEntries(ALL_OUTREACH_STATES.map((s) => [s, counts[s] ?? 0]))
  const body = { counts: fullCounts, quota, jobs }
  const spy = vi.fn(() => HttpResponse.json(body))
  server.use(http.get('/api/stats', spy))
  return spy
}

/**
 * Mocks GET /api/jobs to return `jobs` (a JobOut[] - partial objects are
 * fine, callers only need to set the fields their test reads). Returns the
 * vi.fn() spy backing the handler so a test can read `spy.mock.calls.length`
 * to prove polling behaviour (started, stopped, exact interval).
 */
export function mockJobs(jobs = []) {
  const spy = vi.fn(() => HttpResponse.json(jobs))
  server.use(http.get('/api/jobs', spy))
  return spy
}

/**
 * Mocks POST /api/jobs/{kind}. By default responds 200 with
 * `{job_id: jobId}` (JobStartOut). Pass `{conflict: true, jobId}` to
 * instead simulate the 409-already-running response
 * (`{job_id: jobId}` at status 409), which is what `start_job` returns
 * when that kind is already queued/running - not an error to display, per
 * the task brief.
 */
export function mockStartJob(kind, { conflict = false, jobId = 1 } = {}) {
  const spy = vi.fn(() =>
    conflict
      ? HttpResponse.json({ job_id: jobId }, { status: 409 })
      : HttpResponse.json({ job_id: jobId }),
  )
  server.use(http.post(`/api/jobs/${kind}`, spy))
  return spy
}
