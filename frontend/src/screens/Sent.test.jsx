// Sent: what actually left the mailbox (operator request 2026-09-23).
//
// The question this screen answers is not the Applications screen's
// "how is this going?" but "what did I actually send that person, and
// did they write back?" - so the letter itself is the page, and the list
// is how you choose which one to read.
import { expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { renderWithQuery } from '../test/render'
import { mockApplications, mockThread } from '../test/mocks'
import Sent from './Sent'

const render = () => renderWithQuery(<MemoryRouter><Sent /></MemoryRouter>)

const app = (over = {}) => ({
  outreach_id: 1,
  company_name: 'Mudrex',
  founder_name: 'Edul Patel',
  to: 'edul@mudrex.com',
  state: 'sent',
  outcome: 'sent',
  subject: 'SDE intern inquiry at Mudrex',
  sent_at: '2026-09-22T15:38:10Z',
  skipped_at: null,
  last_error: null,
  ...over,
})

const thread = (over = {}) => ({
  outreach_id: 1,
  company_name: 'Mudrex',
  founder_name: 'Edul Patel',
  to: 'edul@mudrex.com',
  state: 'sent',
  gmail_url: 'https://mail.google.com/mail/u/0/#inbox/abc',
  messages: [
    {
      id: 19,
      direction: 'outbound',
      sequence_step: 0,
      subject: 'SDE intern inquiry at Mudrex',
      body: 'I read your launch post about letting users create their own trading bots.',
      sent_at: '2026-09-22T15:38:10Z',
      received_at: null,
      label: null,
      confidence: null,
      rationale: null,
      corrected: false,
    },
  ],
  ...over,
})

it('lists only the letters that actually went out', async () => {
  mockApplications([
    app(),
    app({ outreach_id: 2, outcome: 'waiting', state: 'pending_approval',
          company_name: 'Vahan', founder_name: 'Madhav Krishna', sent_at: null }),
    app({ outreach_id: 3, outcome: 'rejected', state: 'discarded',
          company_name: 'Rejected Co', founder_name: 'Rej Ected', sent_at: null }),
  ])
  mockThread(1, thread())
  render()
  expect(await screen.findByText('Mudrex')).toBeInTheDocument()
  expect(screen.queryByText('Vahan')).not.toBeInTheDocument()
  expect(screen.queryByText('Rejected Co')).not.toBeInTheDocument()
})

it('opens the newest letter on arrival, so the page reads as a letter', async () => {
  // Not a list with an empty half. The most recent send is the one being
  // thought about.
  mockApplications([
    app({ outreach_id: 1, sent_at: '2026-09-20T10:00:00Z' }),
    app({ outreach_id: 2, company_name: 'Lokal', founder_name: 'Jani Pasha',
          sent_at: '2026-09-22T10:00:00Z' }),
  ])
  const newest = mockThread(2, thread({ outreach_id: 2, company_name: 'Lokal',
                                        founder_name: 'Jani Pasha' }))
  render()
  await waitFor(() => expect(newest).toHaveBeenCalled())
})

it('shows the letter as it was sent, in full', async () => {
  mockApplications([app()])
  mockThread(1, thread())
  render()
  expect(
    await screen.findByText(/I read your launch post about letting users create their own trading bots/),
  ).toBeInTheDocument()
})

it('a reply sits under the letter that prompted it', async () => {
  mockApplications([app({ state: 'replied' })])
  mockThread(1, thread({
    state: 'replied',
    messages: [
      ...thread().messages,
      {
        id: 20, direction: 'inbound', sequence_step: 0, subject: 'Re: SDE intern',
        body: 'Send me your resume again.', sent_at: null,
        received_at: '2026-09-23T09:00:00Z', label: 'INTERESTED',
        confidence: 0.9, rationale: 'asks for more', corrected: false,
      },
    ],
  }))
  render()
  expect(await screen.findByText('Send me your resume again.')).toBeInTheDocument()
  expect(screen.getByText('Their reply')).toBeInTheDocument()
})

it('never calls a draft "sent"', async () => {
  // An outbound row with no `sent_at` is still a draft. On the Sent page
  // that is the one lie the screen must not tell.
  mockApplications([app()])
  mockThread(1, thread({
    messages: [
      ...thread().messages,
      { id: 21, direction: 'outbound', sequence_step: 1, subject: 'Follow-up',
        body: 'A follow-up nobody has sent yet.', sent_at: null, received_at: null,
        label: null, confidence: null, rationale: null, corrected: false },
    ],
  }))
  render()
  await screen.findByText(/I read your launch post/)
  expect(screen.queryByText('A follow-up nobody has sent yet.')).not.toBeInTheDocument()
})

it('picking another letter reads that one instead', async () => {
  mockApplications([
    app({ outreach_id: 1, sent_at: '2026-09-22T10:00:00Z' }),
    app({ outreach_id: 2, company_name: 'Lokal', founder_name: 'Jani Pasha',
          sent_at: '2026-09-20T10:00:00Z' }),
  ])
  mockThread(1, thread())
  const older = mockThread(2, thread({
    outreach_id: 2, company_name: 'Lokal', founder_name: 'Jani Pasha',
    messages: [{ ...thread().messages[0], id: 30, body: 'A different letter entirely.' }],
  }))
  render()
  await userEvent.click(await screen.findByText('Lokal'))
  await waitFor(() => expect(older).toHaveBeenCalled())
  expect(await screen.findByText('A different letter entirely.')).toBeInTheDocument()
})

it('says which of the four sent states each letter is in', async () => {
  // `sent`, `replied`, `closed` and `bounced` are one outcome and four
  // different things to know.
  mockApplications([app({ state: 'bounced' })])
  mockThread(1, thread({ state: 'bounced' }))
  render()
  expect(await screen.findAllByText('Address bounced')).not.toHaveLength(0)
})

it('links out to the real Gmail thread', async () => {
  mockApplications([app()])
  mockThread(1, thread())
  render()
  const link = await screen.findByRole('link', { name: /open in gmail/i })
  expect(link).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#inbox/abc')
})

it('says nothing has been sent yet rather than showing an empty page', async () => {
  mockApplications([app({ outcome: 'waiting', state: 'pending_approval', sent_at: null })])
  render()
  expect(await screen.findByText(/nothing has been sent yet/i)).toBeInTheDocument()
})
