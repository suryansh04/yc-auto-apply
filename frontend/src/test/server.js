// Shared MSW server for tests that need it (hooks tests, future screen
// tests in Tasks 15-17). Started/stopped centrally by ./setup.js so every
// test file just imports `server` and calls `server.use(...)` per test.
import { setupServer } from 'msw/node'

export const server = setupServer()
