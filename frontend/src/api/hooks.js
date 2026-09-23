// TanStack Query hooks over ./client.js - the one seam Tasks 15-17's
// screens are built on. Query keys are plain arrays so invalidation below
// can target them precisely; defaults (retry: see shouldRetry below,
// staleTime: 5000) are set once here rather than per-call.

import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiGet, apiPost } from './client'

// T17-1 (coordinator ruling, task-17-review.md): a flat `retry: 1` retried
// every failure once, including a 404/422/409 the backend will never stop
// returning - measured directly on Thread's not-found screen, a genuine
// ~1s backoff delay before the operator ever saw "no such outreach".
// Retry once, but only for failures that might resolve on their own - a
// network error, or a 5xx the backend might recover from - never a 4xx
// ApiError, which is the server's final, considered answer about this
// request and will not change on a second try.
function shouldRetry(failureCount, error) {
  if (failureCount >= 1) return false
  if (error instanceof ApiError && error.status < 500) return false
  return true
}

// Exported (not just used internally) so a test can assert the real
// defaults directly, and so main.jsx's app-wide QueryClient and any
// test's "real" QueryClient are built from the exact same object rather
// than two copies that could drift apart unnoticed.
export const QUERY_DEFAULTS = { retry: shouldRetry, staleTime: 5000 }

/**
 * The one QueryClient factory the app itself uses (main.jsx). Tests that
 * want to assert the real defaults - as opposed to renderWithQuery's
 * `retry: false` test client - should call this rather than building
 * their own `new QueryClient(...)` inline, so the two never drift apart.
 */
export function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { ...QUERY_DEFAULTS } } })
}

// ---- queries ----------------------------------------------------------

export function useStats() {
  return useQuery({ queryKey: ['stats'], queryFn: () => apiGet('/stats'), ...QUERY_DEFAULTS })
}

// The gate the whole app sits behind. Cheap, and re-asked whenever the
// window regains focus, so coming back from Google's consent screen in
// another tab lands on a fresh answer rather than a stale "not
// connected".
export function useGmailStatus() {
  return useQuery({
    queryKey: ['gmail-status'],
    queryFn: () => apiGet('/gmail/status'),
    refetchOnWindowFocus: true,
    ...QUERY_DEFAULTS,
  })
}

// Starting the flow is a navigation, not a fetch: the API hands back
// Google's URL and the browser goes there. `window.location.assign` (not
// a new tab) so the consent screen and the redirect back happen in one
// place and no popup blocker is involved.
export function useConnectGmail() {
  return useMutation({
    mutationFn: () => apiPost('/gmail/connect'),
    onSuccess: (data) => {
      if (data?.auth_url) window.location.assign(data.auth_url)
    },
  })
}

/**
 * POST /api/gmail/disconnect - forget the stored token.
 *
 * Invalidating the status is the whole effect on this side: the shell
 * gates on `connected`, so the next status read is what sends the
 * operator back to the connect screen. Nothing about the token was ever
 * here to clear.
 */
export function useDisconnectGmail() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost('/gmail/disconnect'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gmail-status'] }),
  })
}

export function useQueue() {
  return useQuery({ queryKey: ['queue'], queryFn: () => apiGet('/queue'), ...QUERY_DEFAULTS })
}

// The letters approved and not yet sent. Same shape as the queue - it is
// the same thing at a later moment.
export function useApproved() {
  return useQuery({ queryKey: ['approved'], queryFn: () => apiGet('/approved'), ...QUERY_DEFAULTS })
}

export function useQueueItem(id) {
  return useQuery({
    queryKey: ['queue', id],
    queryFn: () => apiGet(`/queue/${id}`),
    enabled: id != null,
    ...QUERY_DEFAULTS,
  })
}

export function useManual() {
  return useQuery({ queryKey: ['manual'], queryFn: () => apiGet('/manual'), ...QUERY_DEFAULTS })
}

// Polls every 2s ONLY while at least one job in the last response is
// `running` (Task 15's Pipeline screen needs live progress; every other
// screen just wants the list). `refetchInterval` as a function is
// re-evaluated after every fetch, so polling starts the moment a job
// starts running and stops the moment the last one finishes - no separate
// "am I still polling" state to track by hand.
function jobsRefetchInterval(query) {
  const jobs = query.state.data
  return Array.isArray(jobs) && jobs.some((j) => j.status === 'running') ? 2000 : false
}

export function useJobs() {
  return useQuery({
    queryKey: ['jobs'],
    queryFn: () => apiGet('/jobs'),
    refetchInterval: jobsRefetchInterval,
    ...QUERY_DEFAULTS,
  })
}

function contactsQueryString(filters = {}) {
  const params = new URLSearchParams()
  for (const key of ['state', 'hiring', 'batch', 'email_status']) {
    const value = filters[key]
    if (value !== undefined && value !== null && value !== '') params.set(key, value)
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export function useContacts(filters = {}) {
  return useQuery({
    queryKey: ['contacts', filters],
    queryFn: () => apiGet(`/contacts${contactsQueryString(filters)}`),
    ...QUERY_DEFAULTS,
  })
}

export function useThread(id) {
  return useQuery({
    queryKey: ['thread', id],
    queryFn: () => apiGet(`/outreach/${id}/thread`),
    enabled: id != null,
    ...QUERY_DEFAULTS,
  })
}

// ---- mutations ----------------------------------------------------------
// Each mutation invalidates exactly the queries its write affects on the
// backend - see the routers' own docstrings (queue.py, manual.py,
// jobs.py, thread.py) for which state each route actually changes.

export function useApprove() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/approve`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['approved'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useEdit() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, subject, body }) => apiPost(`/outreach/${id}/edit`, { subject, body }),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['queue', id] })
      // An approved letter is editable too (2026-09-22), so the Approved
      // screen's copy of the text is stale after a save.
      queryClient.invalidateQueries({ queryKey: ['approved'] })
    },
  })
}

/**
 * POST /api/outreach/{id}/unapprove - send an approved letter back to the
 * review queue. Neither sending it nor discarding it: it leaves the
 * Approved screen and reappears in Review with its text untouched.
 */
// The resume PDF `send` attaches to every letter. `useResumeStatus` also
// carries the parsed-claim count, because replacing the file does NOT
// reparse it and the two can silently disagree.
// Every application with the OUTCOME it ended in - a different question
// from `useContacts`, which filters by raw state. Fetched whole and
// filtered on the screen, so the tab counts are right whichever tab is
// open.
export function useApplications() {
  return useQuery({
    queryKey: ['applications'],
    queryFn: () => apiGet('/applications'),
    ...QUERY_DEFAULTS,
  })
}

export function useResumeStatus() {
  return useQuery({
    queryKey: ['resume-status'],
    queryFn: () => apiGet('/resume/status'),
    ...QUERY_DEFAULTS,
  })
}

/**
 * POST /api/resume - replace the attached PDF. Multipart, so it does not
 * go through `apiPost` (which sends JSON); the error shape is the same
 * `{detail}` FastAPI raises everywhere else.
 */
/**
 * POST /api/resume/parse - read the resume and replace the claims a
 * letter may assert about the operator.
 *
 * An upload now does this by itself (2026-09-23), so this is only the
 * way to try again when the parse failed and the file is already saved.
 * Invalidating the status is what updates the claim count everywhere it
 * is shown.
 */
export function useParseResume(options = {}) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost('/resume/parse'),
    ...options,
    onSuccess: (data, vars, ctx) => {
      queryClient.invalidateQueries({ queryKey: ['resume-status'] })
      options.onSuccess?.(data, vars, ctx)
    },
  })
}

export function useReplaceResume() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (file) => {
      const form = new FormData()
      form.append('file', file)
      const response = await fetch('/api/resume', { method: 'POST', body: form })
      const data = await response.json().catch(() => ({}))
      // Same (status, body) shape every other failure in this app has.
      if (!response.ok) throw new ApiError(response.status, data)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['resume-status'] })
      // Every preview footer names the attachment.
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['approved'] })
    },
  })
}

export function useUnapprove() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/unapprove`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['approved'] })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useReject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/reject`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useRedraft() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/redraft`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

/**
 * POST /api/outreach/{id}/check - re-run the checks and return what they
 * say. Writes nothing and refuses nothing (operator decision,
 * 2026-09-22): the notes are advice, so asking for them can never block
 * the person who signs the letter. Optionally judges text being typed
 * rather than the stored draft.
 */
export function useCheckDraft(options = {}) {
  return useMutation({
    mutationFn: ({ id, subject, body }) =>
      apiPost(`/outreach/${id}/check`, subject != null || body != null ? { subject, body } : {}),
    ...options,
  })
}

/**
 * POST /api/outreach/{id}/suggest - what to write instead, for each span
 * `/check` highlighted. A separate request from the check on purpose: the
 * checks are deterministic and instant, this one waits on Sarvam, and the
 * highlights must not wait with it.
 *
 * Answers `{suggestions: [{index, quote, message, replacement, why}],
 * unavailable}`. `index` is a position in the check's `highlights`, so a
 * rewrite is paired with its note without matching on text.
 */
/**
 * POST /api/outreach/{id}/generic-line - swap the company paragraph for
 * the written generic one. An edit like any other: saved, validated,
 * never refused, and it answers with the checks' notes.
 *
 * Invalidates the queue and the approved list because the stored body
 * changed - this is the one "suggestion" on the screen that writes.
 */
export function useGenericLine(options = {}) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/generic-line`),
    ...options,
    onSuccess: (data, id, ctx) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['approved'] })
      options.onSuccess?.(data, id, ctx)
    },
  })
}

/**
 * POST /api/outreach/{id}/insight - a company paragraph written from the
 * company's own YC description.
 *
 * Operator request (2026-09-23): the AI suggestion tab on that paragraph
 * was asking the reviser, which only rewrites what a check flagged, so it
 * had nothing to offer. This asks the other question. Advice like
 * `useSuggestFixes`: it never writes, and "nothing to offer" comes back
 * as a 200 with a reason rather than an error.
 */
export function useWriteInsight(options = {}) {
  return useMutation({
    mutationFn: (id) => apiPost(`/outreach/${id}/insight`),
    ...options,
  })
}

export function useSuggestFixes(options = {}) {
  return useMutation({
    mutationFn: ({ id, subject, body }) =>
      apiPost(`/outreach/${id}/suggest`, subject != null || body != null ? { subject, body } : {}),
    ...options,
  })
}

export function useSetEmail() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, address, giveUp }) =>
      apiPost(`/outreach/${id}/email`, giveUp ? { give_up: true } : { address }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['manual'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useSend() {
  const queryClient = useQueryClient()
  return useMutation({
    // `mutate()` sends everything approved; `mutate({ outreachId })` sends
    // exactly one. `limit` caps a batch run, as the CLI's --limit does.
    mutationFn: (vars) => {
      if (vars == null) return apiPost('/send', undefined)
      if (typeof vars === 'number') return apiPost('/send', { limit: vars })
      const body = {}
      if (vars.limit != null) body.limit = vars.limit
      if (vars.outreachId != null) body.outreach_id = vars.outreachId
      return apiPost('/send', Object.keys(body).length ? body : undefined)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['approved'] })
    },
  })
}

export function useStartJob() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, createdBy }) =>
      apiPost(`/jobs/${kind}`, createdBy != null ? { created_by: createdBy } : undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/**
 * POST /api/messages/{messageId}/label - corrects a reply's classified
 * label (`app.api.routers.thread.correct_label`).
 *
 * `mutate({ messageId, label, threadId })` - `threadId` is REQUIRED even
 * though the backend route takes no outreach/thread id: the response is a
 * bare `OkOut` with nothing to invalidate by, so the caller (Task 17's
 * thread screen, which already has the thread's id in scope from its own
 * route param) must pass it through as a mutation variable purely so this
 * hook knows which `['thread', id]` query to invalidate. Omitting it
 * invalidates `['thread', undefined]`, which matches nothing.
 */
export function useSetLabel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ messageId, label }) => apiPost(`/messages/${messageId}/label`, { label }),
    // onSuccess's second argument is always the variables object passed
    // to mutate(), per TanStack Query's mutation contract.
    onSuccess: (_data, { threadId }) => {
      queryClient.invalidateQueries({ queryKey: ['thread', threadId] })
    },
  })
}
