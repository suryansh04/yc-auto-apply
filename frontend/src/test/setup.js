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

// ---- jsdom gaps the shadcn/Radix components rely on -------------------
//
// These are browser APIs jsdom does not implement at all. They are not
// mocks of our own behaviour: every one of them is a real API the
// component would use in a browser, stubbed to the "no support" answer
// jsdom should already be giving.
//
// `matchMedia` - shadcn's sidebar asks it whether the viewport is mobile
// (src/hooks/use-mobile.js). Without it every render that mounts the app
// shell throws.
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}

// Pointer capture + scrollIntoView - Radix's Select uses both while
// opening its listbox. jsdom implements neither.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

// ResizeObserver - recharts' responsive container (the shadcn chart) and
// Radix's scroll area both construct one on mount.
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
