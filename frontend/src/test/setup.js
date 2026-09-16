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
import { cleanup } from '@testing-library/react'
import { server } from './server'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// vite.config.js sets test.globals: false, so @testing-library/react's own
// auto-cleanup (which only registers when it finds a *global* afterEach)
// never fires. Without this, a screen test file with more than one
// render() call per file leaks the previous test's DOM into the next
// test - "Found multiple elements" failures that have nothing to do with
// the component under test. Task 15's Pipeline.test.jsx is the first file
// with multiple full renders per file, which is what surfaced this.
afterEach(() => cleanup())
