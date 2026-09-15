// Global Vitest setup (registered as vite.config.js's test.setupFiles).
// Owns the ONE shared MSW server's lifecycle so every test file just
// imports `server` from ./server.js and calls `server.use(...)` for its
// own handlers, instead of each file standing up its own setupServer()
// instance (that was tried first and reverted - see task-14-report.md's
// "Fix round 1": two listening instances is avoided by having exactly
// one, not by giving every file its own).
// onUnhandledRequest: 'error' is deliberate - a test that hits an
// endpoint nobody mocked should fail loudly, not silently resolve to a
// generic network error.
import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './server'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
