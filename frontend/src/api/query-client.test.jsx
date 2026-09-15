// Pins QUERY_DEFAULTS itself: the real app QueryClient (main.jsx, via
// createQueryClient()) must carry retry: 1 / staleTime: 5000, and the
// test-only client renderWithQuery builds must override retry to false -
// so neither can be silently zeroed out without a test failing.
import { expect, it } from 'vitest'
import { createQueryClient, QUERY_DEFAULTS } from './hooks'
import { renderWithQuery } from '../test/render'

it('QUERY_DEFAULTS is retry: 1, staleTime: 5000', () => {
  expect(QUERY_DEFAULTS).toEqual({ retry: 1, staleTime: 5000 })
})

it('the real app QueryClient (createQueryClient, used by main.jsx) carries QUERY_DEFAULTS', () => {
  const client = createQueryClient()
  const defaults = client.getDefaultOptions().queries
  expect(defaults.retry).toBe(1)
  expect(defaults.staleTime).toBe(5000)
})

it("renderWithQuery's test client overrides retry to false", () => {
  const { queryClient } = renderWithQuery(<div />)
  const defaults = queryClient.getDefaultOptions().queries
  expect(defaults.retry).toBe(false)
})
