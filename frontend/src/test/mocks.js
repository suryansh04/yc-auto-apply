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
    attachment_name: 'resume.pdf',
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
/** POST /api/outreach/:id/check - the re-run checks. Advice, never a
 *  refusal, so this always answers 200 with the notes in the body. */
export function mockCheckDraft({ notes = [], highlights = null } = {}) {
  const spy = vi.fn()
  server.use(
    http.post('/api/outreach/:id/check', async ({ params }) => {
      spy(Number(params.id))
      // Each note paired with the words it is about; by default nothing
      // resolves, which is the shape a note about the whole letter has.
      return HttpResponse.json({
        notes,
        highlights: highlights ?? notes.map((message) => ({ message, quote: null })),
      })
    }),
  )
  return spy
}

/**
 * POST /api/outreach/:id/suggest - what Sarvam would write instead, for
 * the spans `/check` highlighted. Advice on top of advice: 200 always,
 * and `index` is a position in the check's `highlights`, never a match on
 * text.
 */
export function mockSuggestFixes({ suggestions = [], unavailable = null } = {}) {
  const spy = vi.fn()
  server.use(
    http.post('/api/outreach/:id/suggest', async ({ params, request }) => {
      spy(Number(params.id), await request.json())
      return HttpResponse.json({ suggestions, unavailable })
    }),
  )
  return spy
}

/** POST /api/outreach/:id/suggest that fails outright - the hover cards
 *  must fall back to the note alone, and the checks must stay put. */
export function mockSuggestFailure(status = 500) {
  server.use(
    http.post('/api/outreach/:id/suggest', () =>
      HttpResponse.json({ detail: 'nope' }, { status }),
    ),
  )
}

export function mockApproveFailure(status = 500, detail = 'could not approve') {
  server.use(
    http.post('/api/outreach/:id/approve', () => HttpResponse.json({ detail }, { status })),
  )
}

/** Mocks a successful POST /api/outreach/:id/edit. */
export function mockEdit({ notes = [] } = {}) {
  const spy = vi.fn()
  server.use(
    // 2026-09-22: an edit always saves and answers 200 with what the
    // checks made of the new text. A 4xx now means a real failure.
    http.post('/api/outreach/:id/edit', async ({ params, request }) => {
      const body = await request.json()
      spy(Number(params.id), body)
      return HttpResponse.json({ notes })
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

// ---- Task 17: Contacts --------------------------------------------------
// One `ContactOut` (backend/app/api/schemas.py) with every field the
// Contacts screen reads given a sane default.
export function contact(overrides = {}) {
  return {
    outreach_id: 1,
    founder_name: 'Akash Gupta',
    company_name: 'Vahan',
    domain: 'vahan.co',
    state: 'sent',
    batch: 'W24',
    team_size: 12,
    is_hiring: true,
    resolved_email: 'akash@vahan.co',
    email_status: 'resolved',
    ...overrides,
  }
}

/**
 * Mocks GET /api/contacts. Applies the `state`/`hiring`/`batch`/
 * `email_status` query params to `contacts` itself, the same way the
 * real router (app/api/routers/contacts.py) filters server-side - so a
 * test that drives the screen's own filter controls exercises the real
 * request/response round trip, including D19 (hiring narrows, never
 * gates: omitting the param returns everyone, hiring and non-hiring
 * alike). Returns the vi.fn() spy backing the handler.
 */
export function mockContacts(contacts = []) {
  const spy = vi.fn(({ request }) => {
    const url = new URL(request.url)
    const state = url.searchParams.get('state')
    const hiring = url.searchParams.get('hiring')
    const batch = url.searchParams.get('batch')
    const emailStatus = url.searchParams.get('email_status')
    let out = contacts
    if (state !== null) out = out.filter((c) => c.state === state)
    if (hiring !== null) out = out.filter((c) => String(c.is_hiring) === hiring)
    if (batch !== null) out = out.filter((c) => c.batch === batch)
    if (emailStatus !== null) out = out.filter((c) => c.email_status === emailStatus)
    return HttpResponse.json(out)
  })
  server.use(http.get('/api/contacts', spy))
  return spy
}

// ---- Task 17: Thread ----------------------------------------------------
// One `MessageOut` / `ThreadOut` (backend/app/api/schemas.py), every field
// the Thread screen reads given a sane default.
export function message(overrides = {}) {
  return {
    id: 1,
    direction: 'outbound',
    sequence_step: 0,
    subject: 'Quick question about your internship posting',
    body: 'Hi Akash, quick question about your internship posting.',
    sent_at: '2026-01-01T00:00:00Z',
    received_at: null,
    label: null,
    confidence: null,
    rationale: null,
    corrected: false,
    ...overrides,
  }
}

export function thread(overrides = {}) {
  return {
    outreach_id: 1,
    company_name: 'Vahan',
    founder_name: 'Akash Gupta',
    to: 'akash@vahan.co',
    state: 'sent',
    gmail_url: 'https://mail.google.com/mail/u/0/#inbox/abc123',
    messages: [message()],
    ...overrides,
  }
}

/** Mocks GET /api/outreach/:id/thread to always return `threadObj`. */
export function mockThread(id, threadObj) {
  const spy = vi.fn(() => HttpResponse.json(threadObj))
  server.use(http.get(`/api/outreach/${id}/thread`, spy))
  return spy
}

/**
 * Mocks GET /api/outreach/:id/thread to return a different thread on each
 * successive call (last entry repeats once exhausted) - models a label
 * correction's invalidated refetch actually landing with the corrected
 * row, the same shape `mockQueueSequence` gives Task 16's queue tests.
 */
export function mockThreadSequence(id, threads) {
  let call = 0
  const spy = vi.fn(() => {
    const idx = call
    call += 1
    return HttpResponse.json(threads[Math.min(idx, threads.length - 1)])
  })
  server.use(http.get(`/api/outreach/${id}/thread`, spy))
  return spy
}

/** Mocks GET /api/outreach/:id/thread -> 404, the "no such outreach" case. */
export function mockThreadNotFound(id) {
  server.use(
    http.get(`/api/outreach/${id}/thread`, () =>
      HttpResponse.json({ detail: 'no such outreach' }, { status: 404 }),
    ),
  )
}

/**
 * Mocks a successful POST /api/messages/:id/label. The spy is called with
 * `(messageId, label)` - the two things a test needs to assert a
 * correction targeted the right row, matching `mockApprove`'s
 * "spy called with just the meaningful values" convention.
 */
export function mockSetLabel() {
  const spy = vi.fn()
  server.use(
    http.post('/api/messages/:id/label', async ({ params, request }) => {
      const body = await request.json()
      spy(Number(params.id), body.label)
      return HttpResponse.json({ ok: true })
    }),
  )
  return spy
}

/**
 * Mocks POST /api/messages/:id/label rejected as an invalid label - a
 * plain `{detail}` body (the router's own `HTTPException(422, ...)`
 * shape, `app/api/routers/thread.py`'s `correct_label` - NOT the
 * `{errors: [...]}` list shape queue/manual's validators use).
 */
export function mockSetLabelFailure(detail = 'invalid label') {
  server.use(
    http.post('/api/messages/:id/label', () => HttpResponse.json({ detail }, { status: 422 })),
  )
}

// ---- Task 17: Manual email ------------------------------------------------
// One `ManualItemOut` (backend/app/api/schemas.py), every field the
// Manual email screen reads given a sane default.
export function manualItem(overrides = {}) {
  return {
    outreach_id: 1,
    founder_name: 'Akash Gupta',
    company_name: 'Vahan',
    domain: 'vahan.co',
    linkedin_url: 'https://linkedin.com/in/akashgupta',
    candidates: [],
    published_hint: null,
    ...overrides,
  }
}

/** Mocks GET /api/manual to return `items` (ManualItemOut[]). */
export function mockManual(items = []) {
  const spy = vi.fn(() => HttpResponse.json(items))
  server.use(http.get('/api/manual', spy))
  return spy
}

/**
 * Mocks a successful POST /api/outreach/:id/email. The spy is called with
 * `(id, body)` where `body` is exactly what the screen posted -
 * `{address}` for a typed submission or `{give_up: true}` for give up -
 * so a test can assert the request shape directly.
 */
export function mockSetEmail() {
  const spy = vi.fn()
  server.use(
    http.post('/api/outreach/:id/email', async ({ params, request }) => {
      const body = await request.json()
      spy(Number(params.id), body)
      return HttpResponse.json({ ok: true })
    }),
  )
  return spy
}

/**
 * Mocks POST /api/outreach/:id/email rejected by the validator - 422
 * `{errors: [...]}` verbatim, per the backend's own contract
 * (app/api/routers/manual.py raises a plain HTTPException with `detail`
 * for the "not an email address" case, but the frontend's own edit-form
 * error handling already reads `err.errors ?? [err.message]`, so this
 * mirrors the queue's `mockEditFailure` shape for a consistent test
 * story across the two forms).
 */
export function mockSetEmailFailure(errors) {
  server.use(
    http.post('/api/outreach/:id/email', () => HttpResponse.json({ errors }, { status: 422 })),
  )
}


/** POST /api/send - the one irreversible action. Answers with the five
 *  counts `SendResultOut` carries. */
export function mockSend(result = {}) {
  const spy = vi.fn()
  server.use(
    http.post('/api/send', async ({ request }) => {
      spy(await request.json().catch(() => ({})))
      return HttpResponse.json({
        sent: 0, skipped_cap: 0, skipped_not_reservable: 0,
        needs_manual_check: 0, errors: 0, ...result,
      })
    }),
  )
  return spy
}


/** GET /api/approved - the letters approved and not yet sent. Same shape
 *  as the queue, because it is the same thing at a later moment. */
export function mockApproved(items = []) {
  server.use(http.get('/api/approved', () => HttpResponse.json(items)))
}


/** POST /api/outreach/:id/unapprove - back to the review queue. */
export function mockUnapprove() {
  const spy = vi.fn()
  server.use(
    http.post('/api/outreach/:id/unapprove', ({ params }) => {
      spy(Number(params.id))
      return HttpResponse.json({ ok: true })
    }),
  )
  return spy
}


/** GET /api/resume/status - the attached PDF, and how many parsed claims
 *  the drafter is held to (the two can disagree). */
export function mockResumeStatus(over = {}) {
  server.use(
    http.get('/api/resume/status', () =>
      HttpResponse.json({
        configured: true, filename: 'resume.pdf', size_bytes: 164089,
        modified_at: '2026-09-13T02:23:00Z', claims: 12, ...over,
      }),
    ),
  )
}

/** POST /api/resume - replace the attached PDF. */
export function mockReplaceResume({ status = 200, detail = null, ...over } = {}) {
  const spy = vi.fn()
  server.use(
    // The raw multipart body, not `request.formData()`: undici's parser
    // asserts on jsdom's File objects and throws inside the handler.
    // The filename and the bytes are both in the raw text anyway.
    http.post('/api/resume', async ({ request }) => {
      spy(await request.text())
      if (status !== 200) return HttpResponse.json({ detail }, { status })
      return HttpResponse.json({
        configured: true, filename: 'resume.pdf', size_bytes: 1024,
        modified_at: '2026-09-22T00:00:00Z', claims: 12, ...over,
      })
    }),
  )
  return spy
}


/** GET /api/applications - every application with the OUTCOME it ended
 *  in (`sent` / `rejected` / `skipped` / `waiting` / `not_started`). */
export function mockApplications(items = []) {
  server.use(http.get('/api/applications', () => HttpResponse.json(items)))
}
