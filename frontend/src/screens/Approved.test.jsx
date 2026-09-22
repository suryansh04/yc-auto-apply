// The Approved screen: the letters the operator has decided to send,
// before they go. They used to be invisible - the review queue holds only
// undecided drafts, so an approved letter fell out of every screen that
// shows whole letters, which are exactly the ones about to reach a
// founder.
import { expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { renderWithQuery } from '../test/render'
import { mockApproved, mockSend } from '../test/mocks'
import Approved from './Approved'

const render = () => renderWithQuery(<MemoryRouter><Approved /></MemoryRouter>)

const letter = (over = {}) => ({
  outreach_id: 1,
  company_name: 'Vahan',
  to: 'madhav@vahan.ai',
  subject: 'Backend intern at Vahan',
  wire_text: 'Hi Madhav,\n\nYou put the whole thing on a voice call.\n\nSuryansh',
  ...over,
})

it('lists the approved letters with who they go to', async () => {
  mockApproved([
    letter(),
    letter({ outreach_id: 2, company_name: 'Lokal', to: 'jani@lokal.in',
             subject: 'Lokal internship question' }),
  ])
  render()
  expect(await screen.findByText('Backend intern at Vahan')).toBeInTheDocument()
  expect(screen.getByText('Lokal internship question')).toBeInTheDocument()
  expect(screen.getByText(/madhav@vahan\.ai/)).toBeInTheDocument()
  expect(screen.getByText(/jani@lokal\.in/)).toBeInTheDocument()
})

it('shows the composed wire text, not a summary row', async () => {
  // Ruling R2: what is approved is byte-for-byte what is sent, so this
  // last look has to be at the real thing.
  mockApproved([letter()])
  render()
  expect(await screen.findByText(/You put the whole thing on a voice call/)).toBeInTheDocument()
})

it('sends them all, after asking, and reports what happened', async () => {
  mockApproved([letter(), letter({ outreach_id: 2 })])
  const send = mockSend({ sent: 2 })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  render()
  await userEvent.click(await screen.findByRole('button', { name: /send 2 now/i }))
  await waitFor(() => expect(send).toHaveBeenCalled())
  expect(confirm.mock.calls[0][0]).toMatch(/send 2 letters now/i)
  expect(await screen.findByText('2 letters sent')).toBeInTheDocument()
  confirm.mockRestore()
})

it('does not send when the confirmation is declined', async () => {
  mockApproved([letter()])
  const send = mockSend({ sent: 1 })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  render()
  await userEvent.click(await screen.findByRole('button', { name: /send 1 now/i }))
  expect(send).not.toHaveBeenCalled()
  confirm.mockRestore()
})

it('does not count a letter that still has the fill-in paragraph', async () => {
  // `send_approved` refuses a body carrying the marker, so offering to
  // send it would promise something that cannot happen.
  mockApproved([
    letter(),
    letter({ outreach_id: 2, wire_text: 'Hi there,\n\n>>> YOUR TURN: are you their user?' }),
  ])
  render()
  expect(await screen.findByRole('button', { name: /send 1 now/i })).toBeInTheDocument()
  expect(screen.getByText(/1 letter needs your paragraph/i)).toBeInTheDocument()
  expect(screen.getByText(/will not send yet/i)).toBeInTheDocument()
})

it('offers no send at all when every letter is blocked', async () => {
  mockApproved([letter({ wire_text: 'Hi there,\n\n>>> YOUR TURN: are you their user?' })])
  render()
  expect(await screen.findByRole('button', { name: /send 0 now/i })).toBeDisabled()
})

it('says where letters come from when there are none', async () => {
  mockApproved([])
  render()
  expect(await screen.findByText(/nothing approved yet/i)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /review/i })).toHaveAttribute('href', '/queue')
})
