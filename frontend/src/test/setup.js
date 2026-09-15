// Global Vitest setup (registered as vite.config.js's test.setupFiles).
// MSW server lifecycle is deliberately NOT started here: each test file
// owns its own beforeAll/afterEach/afterAll around the server it uses
// (client.test.js sets up an ad-hoc one per the brief; other test files
// import the shared instance from ./server.js) so two MSW node instances
// are never listening at once.
import '@testing-library/jest-dom/vitest'
