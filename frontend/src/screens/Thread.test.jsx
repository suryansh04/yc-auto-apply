// Task 17: the Thread screen - the per-outreach timeline. Every outbound
// message and every reply in time order, a reply's label with its
// confidence/rationale, a one-click correction control
// (`useSetLabel({messageId, label, threadId})`), a machine label visually
// distinct from a human one, and a link out to Gmail.
import { expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { renderWithQuery } from '../test/render'
import {
  message,
  mockSetLabel,
  mockSetLabelFailure,
  mockThread,
  mockThreadNotFound,
  mockThreadSequence,
  thread,
} from '../test/mocks'
import Thread from './Thread'

function renderThread(id = 1) {
  return renderWithQuery(
    <MemoryRouter initialEntries={[`/thread/${id}`]}>
      <Routes>
        <Route path="/thread/:id" element={<Thread />} />
      </Routes>
    </MemoryRouter>,
  )
}

// --- the brief's four ------------------------------------------------------

it('shows outbound and inbound messages in order', async () => {
  mockThread(
    1,
    thread({
      messages: [
        message({ id: 1, direction: 'outbound', body: 'Quick question', sent_at: '2026-01-01T00:00:00Z' }),
        message({
          id: 2,
          direction: 'inbound',
          body: 'Sure, happy to chat',
          sent_at: null,
          received_at: '2026-01-02T00:00:00Z',
          label: 'INTERESTED',
          confidence: 0.9,
        }),
      ],
    }),
  )
  renderThread(1)
  const items = await screen.findAllByRole('listitem')
  expect(items).toHaveLength(2)
  expect(items[0]).toHaveTextContent('Quick question')
  expect(items[1]).toHaveTextContent('Sure, happy to chat')
})

it('shows the label with its rationale', async () => {
  mockThread(
    1,
    thread({
      messages: [
        message({
          id: 2,
          direction: 'inbound',
          body: 'Not interested right now',
          label: 'REJECTED',
          confidence: 0.8,
          rationale: 'explicitly said not looking',
        }),
      ],
    }),
  )
  renderThread(1)
  expect(await screen.findByText('REJECTED', { selector: '.label-name' })).toBeInTheDocument()
  expect(screen.getByText(/explicitly said not looking/)).toBeInTheDocument()
})

it('corrects a label in one click', async () => {
  const setLabel = mockSetLabel()
  mockThreadSequence(1, [
    thread({ messages: [message({ id: 2, direction: 'inbound', body: 'hm', label: 'UNCLEAR' })] }),
    thread({
      messages: [message({ id: 2, direction: 'inbound', body: 'hm', label: 'INTERESTED', corrected: true })],
    }),
  ])
  renderThread(1)
  await screen.findByText('UNCLEAR', { selector: '.label-name' })

  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: /correct label/i }),
    'INTERESTED',
  )

  expect(setLabel).toHaveBeenCalledWith(2, 'INTERESTED')
  expect(await screen.findByText(/corrected by operator/i)).toBeInTheDocument()
})

it('shows a 422 from a rejected label correction', async () => {
  mockSetLabelFailure('invalid label: not one of the six')
  mockThread(
    1,
    thread({ messages: [message({ id: 2, direction: 'inbound', body: 'hm', label: 'UNCLEAR' })] }),
  )
  renderThread(1)
  await screen.findByText('UNCLEAR', { selector: '.label-name' })

  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: /correct label/i }),
    'INTERESTED',
  )

  expect(await screen.findByText(/invalid label: not one of the six/i)).toBeInTheDocument()
  // the rejected correction never happened - the message still shows its
  // original label, not a phantom "INTERESTED"
  expect(screen.getByText('UNCLEAR', { selector: '.label-name' })).toBeInTheDocument()
})

it('links out to the gmail thread', async () => {
  mockThread(1, thread({ gmail_url: 'https://mail.google.com/mail/u/0/#inbox/abc123' }))
  renderThread(1)
  const link = await screen.findByRole('link', { name: /gmail/i })
  expect(link).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#inbox/abc123')
})

// --- beyond the brief -------------------------------------------------------

it('renders a machine label distinctly from a human label, not just by text', async () => {
  mockThread(
    1,
    thread({
      messages: [
        message({ id: 2, direction: 'inbound', body: 'auto reply', label: 'AUTO_REPLY' }),
        message({
          id: 3,
          direction: 'inbound',
          body: 'a real reply',
          label: 'INTERESTED',
          received_at: '2026-01-03T00:00:00Z',
        }),
      ],
    }),
  )
  renderThread(1)
  await waitFor(() => expect(document.querySelector('.label-machine')).toBeTruthy())

  const machineLabel = document.querySelector('.label-machine')
  const humanLabel = document.querySelector('.label-human')
  expect(machineLabel).toBeTruthy()
  expect(humanLabel).toBeTruthy()
  expect(machineLabel.className).not.toBe(humanLabel.className)
})

it('posts a correction to the right message id when a thread has several, and invalidates the thread', async () => {
  const setLabel = mockSetLabel()
  mockThreadSequence(1, [
    thread({
      messages: [
        message({ id: 10, direction: 'inbound', body: 'first reply', label: 'UNCLEAR' }),
        message({
          id: 20,
          direction: 'inbound',
          body: 'second reply',
          label: 'UNCLEAR',
          received_at: '2026-01-04T00:00:00Z',
        }),
      ],
    }),
    thread({
      messages: [
        message({ id: 10, direction: 'inbound', body: 'first reply', label: 'UNCLEAR' }),
        message({
          id: 20,
          direction: 'inbound',
          body: 'second reply',
          label: 'REFERRAL',
          corrected: true,
          received_at: '2026-01-04T00:00:00Z',
        }),
      ],
    }),
  ])
  renderThread(1)
  await screen.findAllByText('UNCLEAR')

  const items = screen.getAllByRole('listitem')
  await userEvent.selectOptions(
    within(items[1]).getByRole('combobox', { name: /correct label/i }),
    'REFERRAL',
  )

  expect(setLabel).toHaveBeenCalledWith(20, 'REFERRAL')
  expect(setLabel).not.toHaveBeenCalledWith(10, 'REFERRAL')
  // the refetch (invalidation) landed a thread where message 20 (not 10)
  // now carries the corrected label
  await waitFor(() => expect(screen.getAllByText(/corrected by operator/i)).toHaveLength(1))
})

it('shows a not-found state for an unknown thread id rather than crashing', async () => {
  mockThreadNotFound(999)
  renderThread(999)
  // T17-1: QUERY_DEFAULTS.retry never retries a 4xx ApiError, so this
  // resolves on the first failed request - no backoff wait needed (the
  // default findByRole timeout is enough, unlike before T17-1).
  const link = await screen.findByRole('link', { name: /back to contacts/i })
  expect(link).toHaveAttribute('href', '/contacts')
  // A dedicated not-found state, not the generic error Banner (which
  // would also happen to contain the words "no such outreach" verbatim,
  // since that's the 404's own error message - a test that only checked
  // for that substring would pass even if the dedicated branch were
  // deleted).
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByText(/could not load thread/i)).not.toBeInTheDocument()
})
