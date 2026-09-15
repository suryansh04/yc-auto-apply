// Pins QUERY_DEFAULTS itself: the real app QueryClient (main.jsx, via
// createQueryClient()) must carry the T17-1 retry predicate (retry once,
// but never for a 4xx ApiError) and staleTime: 5000, and the test-only
// client renderWithQuery builds must override retry to false - so neither
// can be silently zeroed out (or reverted to a flat retry count) without a
// test failing.
import { expect, it } from 'vitest'
import { createQueryClient, QUERY_DEFAULTS } from './hooks'
import { ApiError } from './client'
import { renderWithQuery } from '../test/render'

it('QUERY_DEFAULTS.staleTime is 5000 and retry is a predicate function', () => {
  expect(QUERY_DEFAULTS.staleTime).toBe(5000)
  expect(typeof QUERY_DEFAULTS.retry).toBe('function')
})

it('QUERY_DEFAULTS.retry: a 4xx ApiError is never retried', () => {
  const notFound = new ApiError(404, { detail: 'no such outreach' })
  expect(QUERY_DEFAULTS.retry(0, notFound)).toBe(false)
})

it('QUERY_DEFAULTS.retry: a 5xx ApiError is retried exactly once', () => {
  const serverError = new ApiError(503, { detail: 'unavailable' })
  expect(QUERY_DEFAULTS.retry(0, serverError)).toBe(true)
  expect(QUERY_DEFAULTS.retry(1, serverError)).toBe(false)
})

it('QUERY_DEFAULTS.retry: a plain network error (not an ApiError) is retried exactly once', () => {
  const networkError = new TypeError('Failed to fetch')
  expect(QUERY_DEFAULTS.retry(0, networkError)).toBe(true)
  expect(QUERY_DEFAULTS.retry(1, networkError)).toBe(false)
})

it('the real app QueryClient (createQueryClient, used by main.jsx) carries QUERY_DEFAULTS', () => {
  const client = createQueryClient()
  const defaults = client.getDefaultOptions().queries
  expect(defaults.retry).toBe(QUERY_DEFAULTS.retry)
  expect(defaults.staleTime).toBe(5000)
})

it("renderWithQuery's test client overrides retry to false", () => {
  const { queryClient } = renderWithQuery(<div />)
  const defaults = queryClient.getDefaultOptions().queries
  expect(defaults.retry).toBe(false)
})
