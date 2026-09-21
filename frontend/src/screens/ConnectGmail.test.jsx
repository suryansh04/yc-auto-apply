// The gate: nothing in this app is shown until Gmail is connected, and
// an operator who cannot connect has to be told exactly why.
import { expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { renderWithQuery } from '../test/render'
import { server } from '../test/server'
import App from '../App'

function mockStatus(status) {
  server.use(http.get('/api/gmail/status', () => HttpResponse.json(status)))
}

function mockEmptyDashboard() {
  server.use(
    http.get('/api/stats', () => HttpResponse.json({ counts: {}, quota: {}, jobs: [] })),
    http.get('/api/jobs', () => HttpResponse.json([])),
  )
}

const CONNECTED = {
  configured: true,
  missing_config: [],
  connected: true,
  account: 'operator@example.com',
  can_read_replies: true,
}

const NOT_CONNECTED = { ...CONNECTED, connected: false, can_read_replies: false }

function render(path = '/') {
  return renderWithQuery(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  // `useConnectGmail` navigates the browser; jsdom has no navigation.
  vi.stubGlobal('location', { ...window.location, assign: vi.fn() })
})

it('shows the connect screen instead of the dashboard when Gmail is not connected', async () => {
  mockStatus(NOT_CONNECTED)
  render()
  expect(await screen.findByRole('heading', { name: /connect gmail/i })).toBeInTheDocument()
  expect(screen.queryByText(/letters waiting for you/i)).not.toBeInTheDocument()
})

it('hides the navigation until Gmail is connected', async () => {
  // Links to screens that cannot work yet are an invitation to a dead end.
  mockStatus(NOT_CONNECTED)
  render()
  await screen.findByRole('heading', { name: /connect gmail/i })
  expect(screen.queryByRole('link', { name: 'Review' })).not.toBeInTheDocument()
})

it('shows the dashboard once Gmail is connected', async () => {
  mockStatus(CONNECTED)
  mockEmptyDashboard()
  render()
  expect(await screen.findByRole('heading', { name: 'Pipeline' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Review' })).toBeInTheDocument()
})

it('does not flash the connect screen while the status is still unknown', async () => {
  // An operator who connected months ago must never see "Connect Gmail"
  // on the way in.
  server.use(
    http.get('/api/gmail/status', async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
      return HttpResponse.json(CONNECTED)
    }),
  )
  mockEmptyDashboard()
  render()
  expect(screen.queryByRole('heading', { name: /connect gmail/i })).not.toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: 'Pipeline' })).toBeInTheDocument()
})

it('sends the browser to Google when Connect is pressed', async () => {
  mockStatus(NOT_CONNECTED)
  server.use(
    http.post('/api/gmail/connect', () =>
      HttpResponse.json({ auth_url: 'https://accounts.google.com/o/oauth2/auth?state=abc' }),
    ),
  )
  render()
  await userEvent.click(await screen.findByRole('button', { name: /connect gmail/i }))
  await waitFor(() =>
    expect(window.location.assign).toHaveBeenCalledWith(
      'https://accounts.google.com/o/oauth2/auth?state=abc',
    ),
  )
})

it('treats a send-only token as not connected and says why', async () => {
  // It can send but the reply poller would fail on every run, so the
  // operator is asked to authorise again rather than finding out later.
  mockStatus({ ...CONNECTED, can_read_replies: false })
  render()
  expect(await screen.findByRole('heading', { name: /reconnect gmail/i })).toBeInTheDocument()
  expect(screen.getByText(/cannot read replies/i)).toBeInTheDocument()
})

it('names the missing .env keys when Gmail was never configured', async () => {
  mockStatus({
    configured: false,
    missing_config: ['GMAIL_SENDER_ADDRESS (the Gmail account authorised by gmail-auth)'],
    connected: false,
    account: null,
    can_read_replies: false,
  })
  render()
  expect(await screen.findByRole('heading', { name: /not set up yet/i })).toBeInTheDocument()
  expect(screen.getByText(/GMAIL_SENDER_ADDRESS/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /connect gmail/i })).not.toBeInTheDocument()
})

it('explains a cancelled consent instead of silently showing the button again', async () => {
  mockStatus(NOT_CONNECTED)
  render('/?gmail=cancelled')
  expect(await screen.findByRole('alert')).toHaveTextContent(/closed google/i)
})

it('explains the no-refresh-token case with the fix', async () => {
  mockStatus(NOT_CONNECTED)
  render('/?gmail=no-refresh-token')
  expect(await screen.findByRole('alert')).toHaveTextContent(/myaccount\.google\.com\/permissions/)
})

it('says the API is down rather than asking for Gmail when status cannot be fetched', async () => {
  server.use(http.get('/api/gmail/status', () => HttpResponse.error()))
  render()
  // QUERY_DEFAULTS retries a network error once (T17-1), so the error
  // state is deliberately not immediate.
  expect(
    await screen.findByRole('heading', { name: /api is not answering/i }, { timeout: 4000 }),
  ).toBeInTheDocument()
})
