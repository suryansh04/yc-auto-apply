// The Approved screen: the letters the operator has decided to send,
// before they go. They used to be invisible - the review queue holds only
// undecided drafts, so an approved letter fell out of every screen that
// shows whole letters, which are exactly the ones about to reach a
// founder.
import { beforeEach, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { renderWithQuery } from '../test/render'
import {
  mockApproved,
  mockEdit,
  mockEditFailure,
  mockReplaceResume,
  mockResumeStatus,
  mockSend,
  mockUnapprove,
} from '../test/mocks'
import Approved from './Approved'

const render = () => renderWithQuery(<MemoryRouter><Approved /></MemoryRouter>)

// Every test on this screen renders the resume card, so every test needs
// the endpoint answered (setup.js fails an unhandled request loudly).
beforeEach(() => mockResumeStatus())

const letter = (over = {}) => ({
  outreach_id: 1,
  company_name: 'Vahan',
  to: 'madhav@vahan.ai',
  subject: 'Backend intern at Vahan',
  // The stored body is what the editor seeds from; the wire text is the
  // composed preview with greeting, signature and opt-out line.
  body: 'You put the whole thing on a voice call.',
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
  await userEvent.click(await screen.findByRole('button', { name: /send all 2/i }))
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
  await userEvent.click(await screen.findByRole('button', { name: /send all 1/i }))
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
  expect(await screen.findByRole('button', { name: /send all 1/i })).toBeInTheDocument()
  expect(screen.getByText(/1 letter needs your paragraph/i)).toBeInTheDocument()
  expect(screen.getByText(/will not send yet/i)).toBeInTheDocument()
})

it('offers no send at all when every letter is blocked', async () => {
  mockApproved([letter({ wire_text: 'Hi there,\n\n>>> YOUR TURN: are you their user?' })])
  render()
  expect(await screen.findByRole('button', { name: /send all 0/i })).toBeDisabled()
})

it('says where letters come from when there are none', async () => {
  mockApproved([])
  render()
  expect(await screen.findByText(/nothing approved yet/i)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /review/i })).toHaveAttribute('href', '/queue')
})


// --- per-letter edit and send (operator request 2026-09-22) ----------------

it('sends one letter on its own, after asking', async () => {
  mockApproved([letter(), letter({ outreach_id: 2, to: 'jani@lokal.in' })])
  const send = mockSend({ sent: 1 })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  render()
  const rows = await screen.findAllByRole('button', { name: /^send$/i })
  await userEvent.click(rows[1])
  await waitFor(() => expect(send).toHaveBeenCalled())
  // The one that was asked for, by id - not the whole batch.
  expect(send.mock.calls[0][0]).toEqual({ outreach_id: 2 })
  expect(confirm.mock.calls[0][0]).toMatch(/jani@lokal\.in/)
  confirm.mockRestore()
})

it('will not offer a per-letter send for one that cannot go', async () => {
  mockApproved([letter({ wire_text: 'Hi there,\n\n>>> YOUR TURN: are you their user?' })])
  render()
  expect(await screen.findByRole('button', { name: /^send$/i })).toBeDisabled()
})

it('edits a letter in place and saves it', async () => {
  mockApproved([letter()])
  const edit = mockEdit()
  render()
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
  const body = screen.getByLabelText('Body')
  expect(body).toHaveValue('You put the whole thing on a voice call.')
  await userEvent.clear(body)
  await userEvent.type(body, 'A better sentence.')
  await userEvent.click(screen.getByRole('button', { name: /save/i }))
  await waitFor(() => expect(edit).toHaveBeenCalled())
  // mockEdit spies as (id, body)
  expect(edit.mock.calls[0][0]).toBe(1)
  expect(edit.mock.calls[0][1].body).toBe('A better sentence.')
})

it('keeps the typed text when the validator rejects an edit', async () => {
  mockApproved([letter()])
  mockEditFailure(['contains a semicolon'])
  render()
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
  const body = screen.getByLabelText('Body')
  await userEvent.clear(body)
  await userEvent.type(body, 'Text with a semicolon; in it.')
  await userEvent.click(screen.getByRole('button', { name: /save/i }))
  expect(await screen.findByText('contains a semicolon')).toBeInTheDocument()
  // Still open, still holding what was typed.
  expect(screen.getByLabelText('Body')).toHaveValue('Text with a semicolon; in it.')
})

it('cancelling an edit changes nothing', async () => {
  mockApproved([letter()])
  const edit = mockEdit()
  render()
  await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
  await userEvent.type(screen.getByLabelText('Body'), ' and more')
  await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
  expect(edit).not.toHaveBeenCalled()
  expect(screen.queryByLabelText('Body')).not.toBeInTheDocument()
})


it('sends a letter back to the review queue', async () => {
  // Neither sending nor discarding: reject throws the founder away and
  // redraft spends a model call. This just undoes the decision.
  mockApproved([letter(), letter({ outreach_id: 2 })])
  const unapprove = mockUnapprove()
  render()
  const buttons = await screen.findAllByRole('button', { name: /back to review/i })
  await userEvent.click(buttons[1])
  await waitFor(() => expect(unapprove).toHaveBeenCalledWith(2))
})

it('offers back-to-review even for a letter that cannot be sent', async () => {
  // The blocked one is exactly the letter most likely to need it.
  mockApproved([letter({ wire_text: 'Hi there,\n\n>>> YOUR TURN: are you their user?' })])
  render()
  expect(await screen.findByRole('button', { name: /back to review/i })).toBeEnabled()
  expect(screen.getByRole('button', { name: /^send$/i })).toBeDisabled()
})


// --- the attached resume (operator request 2026-09-22) --------------------

it('names the attached resume and links to it', async () => {
  mockApproved([letter()])
  mockResumeStatus({ filename: 'My_Resume.pdf', size_bytes: 164089 })
  render()
  expect(await screen.findByText(/My_Resume\.pdf/)).toBeInTheDocument()
  // A new tab, not a download: the point is to look at it before sending.
  const view = screen.getByRole('link', { name: /view/i })
  expect(view).toHaveAttribute('href', '/api/resume')
  expect(view).toHaveAttribute('target', '_blank')
})

it('says so when no resume is configured', async () => {
  mockApproved([letter()])
  mockResumeStatus({ configured: false, filename: null, size_bytes: null })
  render()
  expect(await screen.findByText(/no resume file is configured/i)).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /view/i })).not.toBeInTheDocument()
})

it('replaces the resume and warns that the claims are still the old ones', async () => {
  // The consequence nobody would guess: replacing the PDF does not
  // reparse it, so what the drafter may say is unchanged.
  mockApproved([letter()])
  const replace = mockReplaceResume()
  render()
  await screen.findByText(/resume\.pdf/i)
  const input = screen.getByLabelText(/replace the resume/i)
  await userEvent.upload(input, new File(['%PDF-1.4 x'], 'new.pdf', { type: 'application/pdf' }))
  await waitFor(() => expect(replace).toHaveBeenCalled())
  // What is asserted here is that choosing a file posts to the endpoint.
  // That the BYTES arrive intact is the backend's contract and is pinned
  // there (test_replacing_the_resume_writes_the_new_bytes) against a real
  // HTTP client - under jsdom, undici does not serialise a jsdom File
  // into a readable multipart body, so asserting it here would be
  // asserting the harness.
  expect(await screen.findByText(/resume replaced/i)).toBeInTheDocument()
  expect(screen.getByText(/parse-resume/)).toBeInTheDocument()
})

it('shows why a rejected upload was rejected', async () => {
  mockApproved([letter()])
  mockReplaceResume({ status: 400, detail: 'that is not a PDF' })
  render()
  await screen.findByText(/resume\.pdf/i)
  // Named .pdf so the input's own `accept` filter lets it through -
  // userEvent honours that, and the point here is the SERVER's refusal.
  await userEvent.upload(
    screen.getByLabelText(/replace the resume/i),
    new File(['PK zip pretending'], 'cv.pdf', { type: 'application/pdf' }),
  )
  expect(await screen.findByText(/that is not a PDF/i)).toBeInTheDocument()
})
