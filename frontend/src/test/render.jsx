// Test helper for anything using TanStack Query hooks (this task's own
// hooks.test.jsx, and Tasks 15-17's screen tests). retry: false so a
// deliberately-erroring query in a test doesn't sit through react-query's
// default retry/backoff before the assertion can see it fail.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'

export function renderWithQuery(ui) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
    ),
  }
}
