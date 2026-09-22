// Pins every mutation hook's invalidation keys exactly - no more, no
// fewer. hooks.test.jsx already proves useApprove's invalidation causes a
// real refetch end-to-end; this file instead spies directly on
// queryClient.invalidateQueries for every mutation hook (including
// useApprove, for symmetry) so an over-invalidation (e.g. a stray
// ['queue'] tacked onto useSetLabel) fails a test instead of passing
// silently - that's exactly what over-invalidation would do to the
// review screen: refetch the whole queue on every keypress.
import { expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '../test/server'
import {
  useApprove, useEdit, useReject, useRedraft,
  useSetEmail, useSend, useStartJob, useSetLabel,
} from './hooks'

function renderMutationHook(useHook) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const spy = vi.spyOn(queryClient, 'invalidateQueries')
  function wrapper({ children }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  const { result } = renderHook(() => useHook(), { wrapper })
  return { result, spy }
}

const cases = [
  {
    name: 'useApprove',
    hook: useApprove,
    mock: () => http.post('/api/outreach/1/approve', () => HttpResponse.json({ ok: true })),
    variables: 1,
    // ['approved'] joined the set on 2026-09-22: approving is what puts a
    // letter on the Approved screen, so that list is now stale too.
    expectedKeys: [['queue'], ['approved'], ['stats']],
  },
  {
    name: 'useEdit',
    hook: useEdit,
    mock: () => http.post('/api/outreach/1/edit', () => HttpResponse.json({ ok: true })),
    variables: { id: 1, subject: 's', body: 'b' },
    expectedKeys: [['queue'], ['queue', 1]],
  },
  {
    name: 'useReject',
    hook: useReject,
    mock: () => http.post('/api/outreach/1/reject', () => HttpResponse.json({ ok: true })),
    variables: 1,
    // ['approved'] joined the set on 2026-09-22: approving is what puts a
    // letter on the Approved screen, so that list is now stale too.
    expectedKeys: [['queue'], ['stats']],
  },
  {
    name: 'useRedraft',
    hook: useRedraft,
    mock: () => http.post('/api/outreach/1/redraft', () => HttpResponse.json({ ok: true })),
    variables: 1,
    // ['approved'] joined the set on 2026-09-22: approving is what puts a
    // letter on the Approved screen, so that list is now stale too.
    expectedKeys: [['queue'], ['stats']],
  },
  {
    name: 'useSetEmail',
    hook: useSetEmail,
    mock: () => http.post('/api/outreach/1/email', () => HttpResponse.json({ ok: true })),
    variables: { id: 1, address: 'a@b.com' },
    expectedKeys: [['manual'], ['stats']],
  },
  {
    name: 'useSend',
    hook: useSend,
    mock: () => http.post('/api/send', () => HttpResponse.json({ sent: 1 })),
    variables: 5,
    expectedKeys: [['stats'], ['queue'], ['approved']],
  },
  {
    name: 'useStartJob',
    hook: useStartJob,
    mock: () => http.post('/api/jobs/scrape', () => HttpResponse.json({ job_id: 1 })),
    variables: { kind: 'scrape' },
    expectedKeys: [['jobs']],
  },
  {
    name: 'useSetLabel',
    hook: useSetLabel,
    mock: () => http.post('/api/messages/1/label', () => HttpResponse.json({ ok: true })),
    variables: { messageId: 1, label: 'interested', threadId: 5 },
    expectedKeys: [['thread', 5]],
  },
]

it.each(cases)('$name invalidates exactly $expectedKeys and nothing else', async ({ hook, mock, variables, expectedKeys }) => {
  server.use(mock())
  const { result, spy } = renderMutationHook(hook)

  result.current.mutate(variables)
  await waitFor(() => expect(result.current.isSuccess).toBe(true))

  const actualKeys = spy.mock.calls.map((call) => call[0].queryKey)
  expect(actualKeys).toEqual(expectedKeys)
})
