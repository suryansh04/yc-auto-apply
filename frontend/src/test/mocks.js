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

// ---- Task 16: Review Queue --------------------------------------------
// One `QueueItemOut` (backend/app/api/schemas.py), with every field the
// screen reads given a sane default so a test only has to override what
// it actually cares about. `wire_text` deliberately differs from `body`
// by default (greeting + signature wrapped around it) so a test that
// forgets to override either still exercises R2 (preview the composed
// email, never the bare body) rather than passing by accident because
// the two happened to be equal.
export function item(overrides = {}) {
  return {
    outreach_id: 1,
    company_name: 'Vahan',
    company_one_liner: 'Voice AI for blue-collar hiring in India',
    founder_name: 'Akash Gupta',
    founder_title: 'CEO',
    to: 'akash@vahan.co',
    subject: 'Quick question about your internship posting',
    body: 'Body text.',
    wire_text: 'Hi Akash,\n\nBody text.\n\nSuryansh',
    word_count: 42,
    source_kind: 'news',
    source_text: 'OpenAI backs Vahan in $10M seed round',
    source_publication: 'TechCrunch',
    last_error: null,
    critic_verdict: 'PASS',
    critic_note: null,
    sequence_step: 0,
    ...overrides,
  }
}

/**
 * Mocks GET /api/queue to return `items` (QueueItemOut[] - use `item()`
 * to build each one). Returns the vi.fn() spy backing the handler.
 */
export function mockQueue(items = []) {
  const spy = vi.fn(() => HttpResponse.json(items))
  server.use(http.get('/api/queue', spy))
  return spy
}

/**
 * Mocks GET /api/queue to return a different list on each successive
 * call, from `lists` (an array of QueueItemOut[]; the last entry repeats
 * once exhausted). Models what the real backend does after a write: the
 * approved/rejected/redrafted row is no longer `PENDING_APPROVAL`, so
 * `pending()` returns a shorter list on the very next GET
 * (`app/application/review_outreach.py`) - `mockQueue`'s single static
 * list can't reproduce that shift, which is exactly what fix round 1's
 * F1 finding needed a test for. Returns the vi.fn() spy.
 *
 * `delayMs` (fix round 2, F9) holds every call *after* the first open
 * for that long before responding - the initial load is instant, but
 * every refetch triggered by an invalidation is slow, reproducing the
 * "mutation resolved, refetch hasn't" window a settled-id filter has to
 * survive.
 */
export function mockQueueSequence(lists, { delayMs = 0 } = {}) {
  let call = 0
  const spy = vi.fn(async () => {
    const idx = call
    call += 1
    const list = lists[Math.min(idx, lists.length - 1)]
    if (delayMs > 0 && idx > 0) await new Promise((resolve) => setTimeout(resolve, delayMs))
    return HttpResponse.json(list)
  })
  server.use(http.get('/api/queue', spy))
  return spy
}

/**
 * Mocks a successful POST /api/outreach/:id/approve. The returned spy is
 * called with just the numeric outreach id (not the raw MSW request), so
 * a test can assert `expect(approve).toHaveBeenCalledWith(1)` directly,
 * matching what the screen conceptually did ("approved outreach 1").
 * `delayMs` (fix round 1, F5) holds the response open, for tests proving
 * a double keypress/click while a mutation is in flight is a no-op.
 */
export function mockApprove({ delayMs = 0 } = {}) {
  const spy = vi.fn()
  server.use(
    http.post('/api/outreach/:id/approve', async ({ params }) => {
      spy(Number(params.id))
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs))
      return HttpResponse.json({ ok: true })
    }),
  )
  return spy
}

/**
 * Mocks a successful POST /api/outreach/:id/redraft - same shape as
 * `mockApprove`, for tests exercising `actOn`'s shared advance/settle
 * logic through a different action (fix round 3, F9's prune coverage).
 */
export function mockRedraft({ delayMs = 0 } = {}) {
  const spy = vi.fn()
  server.use(
    http.post('/api/outreach/:id/redraft', async ({ params }) => {
      spy(Number(params.id))
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs))
      return HttpResponse.json({ ok: true })
    }),
  )
  return spy
}

/**
 * Mocks a failing POST /api/outreach/:id/approve - `status` defaults to
 * 500 (a generic failure); pass 409 with a `detail` message to simulate
 * the backend's "flagged NEEDS MANUAL WRITE" refusal
 * (app/application/review_outreach.py's `approve`), whose message the
 * screen must show verbatim.
 */
export function mockApproveFailure(status = 500, detail = 'could not approve') {
  server.use(
    http.post('/api/outreach/:id/approve', () => HttpResponse.json({ detail }, { status })),
  )
}

/** Mocks a successful POST /api/outreach/:id/edit. */
export function mockEdit() {
  const spy = vi.fn()
  server.use(
    http.post('/api/outreach/:id/edit', async ({ params, request }) => {
      const body = await request.json()
      spy(Number(params.id), body)
      return HttpResponse.json({ ok: true })
    }),
  )
  return spy
}

/**
 * Mocks POST /api/outreach/:id/edit rejected by the validator - 422
 * `{errors: [...]}` verbatim, per the backend's own contract
 * (app/api/routers/queue.py's `edit`).
 */
export function mockEditFailure(errors) {
  server.use(
    http.post('/api/outreach/:id/edit', () => HttpResponse.json({ errors }, { status: 422 })),
  )
}
