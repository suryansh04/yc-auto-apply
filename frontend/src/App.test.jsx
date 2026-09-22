// The shell: the sidebar, and the account it is sending as.
//
// Operator request (2026-09-23): "give option here for logout too". The
// account line in the footer was the only place in the app that named
// the connected account, and the only way out of it was to stop using
// the app.
import { expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { renderWithQuery } from './test/render'
import {
  mockDisconnectFailure,
  mockDisconnectGmail,
  mockGmailStatus,
  mockJobs,
  mockStats,
} from './test/mocks'
import App from './App'

// The shell renders the Pipeline at "/", so its reads are stubbed too -
// this file is about the footer, not about what is under it.
function render() {
  mockStats()
  mockJobs()
  return renderWithQuery(
    <MemoryRouter initialEntries={['/']}>
      <App />
    </MemoryRouter>,
  )
}

async function openAccountMenu() {
  await userEvent.click(await screen.findByRole('button', { name: /sending as/i }))
}

it('names the account it is sending as', async () => {
  mockGmailStatus()
  render()
  expect(await screen.findByText('suryanshsingh0987@gmail.com')).toBeInTheDocument()
})

it('offers to disconnect from the account line', async () => {
  mockGmailStatus()
  render()
  await openAccountMenu()
  expect(await screen.findByRole('menuitem', { name: /disconnect gmail/i })).toBeInTheDocument()
})

it('asks before disconnecting', async () => {
  // Undoing it costs a trip through Google's consent screen, and the
  // account line is a place the cursor passes on the way to the nav.
  mockGmailStatus()
  const disconnect = mockDisconnectGmail()
  render()
  await openAccountMenu()
  await userEvent.click(await screen.findByRole('menuitem', { name: /disconnect gmail/i }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  expect(disconnect).not.toHaveBeenCalled()
})

it('says what disconnecting does and does not do', async () => {
  // "Disconnect" here means this machine forgets the token, not that
  // Google withdraws the grant. Only the operator can do the second.
  mockGmailStatus()
  render()
  await openAccountMenu()
  await userEvent.click(await screen.findByRole('menuitem', { name: /disconnect gmail/i }))
  expect(await screen.findByText(/forgets the token/i)).toBeInTheDocument()
  expect(screen.getByText(/drafts, approvals and everything already sent are untouched/i))
    .toBeInTheDocument()
  expect(screen.getByRole('link', { name: /myaccount\.google\.com\/permissions/i }))
    .toBeInTheDocument()
})

it('cancelling leaves the account connected', async () => {
  mockGmailStatus()
  const disconnect = mockDisconnectGmail()
  render()
  await openAccountMenu()
  await userEvent.click(await screen.findByRole('menuitem', { name: /disconnect gmail/i }))
  await userEvent.click(await screen.findByRole('button', { name: /cancel/i }))
  expect(disconnect).not.toHaveBeenCalled()
  expect(screen.getByText('suryanshsingh0987@gmail.com')).toBeInTheDocument()
})

it('confirming disconnects', async () => {
  mockGmailStatus()
  const disconnect = mockDisconnectGmail()
  render()
  await openAccountMenu()
  await userEvent.click(await screen.findByRole('menuitem', { name: /disconnect gmail/i }))
  await userEvent.click(await screen.findByRole('button', { name: /^disconnect$/i }))
  await waitFor(() => expect(disconnect).toHaveBeenCalled())
})

it('a failed disconnect says so and keeps the dialog open', async () => {
  // The token is still there, so closing the dialog as if it had worked
  // would be a lie the operator would act on.
  mockGmailStatus()
  mockDisconnectFailure('could not disconnect')
  render()
  await openAccountMenu()
  await userEvent.click(await screen.findByRole('menuitem', { name: /disconnect gmail/i }))
  await userEvent.click(await screen.findByRole('button', { name: /^disconnect$/i }))
  expect(await screen.findByText(/could not disconnect/i)).toBeInTheDocument()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

it('shows no account line until Gmail can both send and read', async () => {
  // A token minted before the read scope existed looks connected and
  // fails only when the reply poller runs - the shell sends it back
  // through consent rather than showing it as good.
  mockGmailStatus({ can_read_replies: false })
  render()
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: /sending as/i })).not.toBeInTheDocument(),
  )
  expect(screen.queryByText('suryanshsingh0987@gmail.com')).not.toBeInTheDocument()
})
