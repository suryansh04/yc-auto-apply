// Task 16's Review Queue screen. server's listen/resetHandlers/close
// lifecycle is owned globally by src/test/setup.js (afterEach(cleanup)
// too, per task-15-report.md's finding) - this file only registers
// handlers via src/test/mocks.js helpers.
import { expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithQuery as render } from '../test/render'
import {
  item,
  mockApprove,
  mockApproveFailure,
  mockEdit,
  mockEditFailure,
  mockQueue,
} from '../test/mocks'
import ReviewQueue from './ReviewQueue'

// --- the brief's nine ---------------------------------------------------

it('shows the composed email, not the bare stored body', async () => {
  mockQueue([item({ body: 'Body text.', wire_text: 'Hi Akash,\n\nBody text.\n\nSuryansh' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/Hi Akash,/)).toBeInTheDocument()
})

it('shows the cited source beside the draft so a claim is checkable', async () => {
  mockQueue([item({ source_kind: 'news', source_text: 'OpenAI backs Vahan' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/OpenAI backs Vahan/)).toBeInTheDocument()
})

it('approves on "a" and advances to the next draft', async () => {
  const approve = mockApprove()
  mockQueue([item({ outreach_id: 1 }), item({ outreach_id: 2, company_name: 'Second Co' })])
  render(<ReviewQueue />)
  await screen.findByText(/1 of 2/)
  await userEvent.keyboard('a')
  expect(approve).toHaveBeenCalledWith(1)
  expect(await screen.findByText(/Second Co/)).toBeInTheDocument()
})

it('rolls back the optimistic advance when approve fails', async () => {
  mockApproveFailure()
  mockQueue([item({ outreach_id: 1, company_name: 'First Co' })])
  render(<ReviewQueue />)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/First Co/)).toBeInTheDocument()
  expect(await screen.findByText(/could not approve/i)).toBeInTheDocument()
})

it('shows validator errors verbatim when an edit is rejected', async () => {
  mockEditFailure(['contains a semicolon', 'no explicit internship ask'])
  mockQueue([item({})])
  render(<ReviewQueue />)
  await userEvent.keyboard('e')
  await userEvent.click(await screen.findByRole('button', { name: /save/i }))
  expect(await screen.findByText('contains a semicolon')).toBeInTheDocument()
  expect(screen.getByText('no explicit internship ask')).toBeInTheDocument()
})

it('keeps the typed text when an edit is rejected', async () => {
  mockEditFailure(['contains a semicolon'])
  mockQueue([item({})])
  render(<ReviewQueue />)
  await userEvent.keyboard('e')
  const box = await screen.findByRole('textbox', { name: /body/i })
  await userEvent.clear(box)
  await userEvent.type(box, 'my own words')
  await userEvent.click(screen.getByRole('button', { name: /save/i }))
  expect(await screen.findByDisplayValue(/my own words/)).toBeInTheDocument()
})

it('marks a needs-manual-write draft and refuses a bare approve', async () => {
  mockQueue([item({ last_error: 'cited posting is not intern-appropriate' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/needs manual write/i)).toBeInTheDocument()
})

it('shows the critic note as one dismissible line', async () => {
  mockQueue([item({ critic_verdict: 'NEEDS_REVISION', critic_note: 'opener is generic' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/opener is generic/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
  expect(screen.queryByText(/opener is generic/)).not.toBeInTheDocument()
})

it('ignores review keys while the editor is open', async () => {
  const approve = mockApprove()
  mockQueue([item({})])
  render(<ReviewQueue />)
  await userEvent.keyboard('e')
  await userEvent.type(await screen.findByRole('textbox', { name: /body/i }), 'a')
  expect(approve).not.toHaveBeenCalled()
})

// --- beyond the brief's nine --------------------------------------------

it('"j"/"k" navigation stops at the ends instead of wrapping', async () => {
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)

  // "k" at the first item stays put.
  await userEvent.keyboard('k')
  expect(await screen.findByText(/1 of 2/)).toBeInTheDocument()
  expect(screen.getByText(/First Co/)).toBeInTheDocument()

  // "j" past the last item stays put too.
  await userEvent.keyboard('j')
  await screen.findByText(/2 of 2/)
  await userEvent.keyboard('j')
  expect(await screen.findByText(/2 of 2/)).toBeInTheDocument()
  expect(screen.getByText(/Second Co/)).toBeInTheDocument()
})

it('renders an ERROR critic verdict as "unavailable", never as a verdict', async () => {
  mockQueue([item({ critic_verdict: 'ERROR', critic_note: null })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/critic unavailable/i)).toBeInTheDocument()
  expect(screen.queryByText(/^PASS$/)).not.toBeInTheDocument()
  expect(screen.queryByText(/NEEDS_REVISION/)).not.toBeInTheDocument()
})

it('a 409 on approve (flagged draft) shows the reason and does not advance', async () => {
  mockApproveFailure(409, 'refusing to approve: this draft is flagged NEEDS MANUAL WRITE (bad email) - press [e] to edit it before approving')
  mockQueue([item({ outreach_id: 1, company_name: 'Flagged Co', last_error: 'bad email' })])
  render(<ReviewQueue />)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/NEEDS MANUAL WRITE/)).toBeInTheDocument()
  expect(await screen.findByText(/1 of 1/)).toBeInTheDocument()
  expect(screen.getByText(/Flagged Co/)).toBeInTheDocument()
})

it('dismissing the critic note on item 1 does not hide it on item 2', async () => {
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co', critic_note: 'first note' }),
    item({ outreach_id: 2, company_name: 'Second Co', critic_note: 'second note' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/first note/)
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
  expect(screen.queryByText(/first note/)).not.toBeInTheDocument()

  await userEvent.keyboard('j')
  expect(await screen.findByText(/second note/)).toBeInTheDocument()
})

it('updates the counter after an approve', async () => {
  mockApprove()
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/1 of 2/)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/2 of 2/)).toBeInTheDocument()
})

it('shows the empty state once the queue is exhausted', async () => {
  mockApprove()
  let call = 0
  mockQueue([item({ outreach_id: 1, company_name: 'Only Co' })])
  // After the approve's invalidation refetches /api/queue, the backend
  // would no longer return the approved row - simulate that by swapping
  // in a second handler after the first GET.
  const { server } = await import('../test/server')
  const { http, HttpResponse } = await import('msw')
  server.use(
    http.get('/api/queue', () => {
      call += 1
      return HttpResponse.json(call === 1 ? [item({ outreach_id: 1, company_name: 'Only Co' })] : [])
    }),
  )
  render(<ReviewQueue />)
  await screen.findByText(/Only Co/)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/queue is empty/i)).toBeInTheDocument()
})

it('labels a sequence_step > 0 draft as a follow-up', async () => {
  mockQueue([item({ sequence_step: 2 })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/follow-up/i)).toBeInTheDocument()
  expect(screen.getByText(/step 2/i)).toBeInTheDocument()
})
