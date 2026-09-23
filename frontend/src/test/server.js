// Shared MSW server for tests that need it (hooks tests, future screen
// tests in Tasks 15-17). Started/stopped centrally by ./setup.js so every
// test file just imports `server` and calls `server.use(...)` per test.
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'

export const server = setupServer(
  // The review screen asks for rewrites on its own, whenever a check
  // came back with words to mark - so every test that opens a draft
  // would otherwise trip `onUnhandledRequest: 'error'` for a call it
  // never asked for. The default answer is "no rewrites", which is what
  // an unconfigured provider says too; `mockSuggestFixes` overrides it
  // where a test is actually about the hover card.
  http.post('/api/outreach/:id/suggest', () =>
    HttpResponse.json({ suggestions: [], unavailable: null }),
  ),
  // Same reason: opening the company paragraph's card asks for a
  // paragraph written from the company's YC page. `mockWriteInsight`
  // overrides this where a test is about that offer.
  http.post('/api/outreach/:id/insight', () =>
    HttpResponse.json({ paragraph: null, why: '', unavailable: null }),
  ),
)
