// Task 17: the Manual email screen - the NEEDS_MANUAL_EMAIL founders.
// A hand-typed address (useSetEmail({id, address})), a give-up control
// (useSetEmail({id, giveUp: true}) -> UNRESOLVED), the candidates already
// tried with their verdicts, and the published-email hint when resolve
// found one.
import { expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithQuery as render } from '../test/render'
import { manualItem, mockManual, mockSetEmail, mockSetEmailFailure } from '../test/mocks'
import ManualEmail from './ManualEmail'

// --- the brief's four -------------------------------------------------------

it('collapses tried candidates behind a count until the reviewer expands them', async () => {
  mockManual([
    manualItem({
      outreach_id: 1,
      founder_name: 'Akash Gupta',
      candidates: [
        { address: 'akash@vahan.co', verdict: 'invalid' },
        { address: 'a.gupta@vahan.co', verdict: 'risky' },
      ],
    }),
  ])
  render(<ManualEmail />)
  const summary = await screen.findByText('2 tried')
  // A collapsed shadcn/Radix collapsible does not render its content at
  // all, so "hidden" is "absent" here rather than "present but not
  // visible" - the point is the same: seven rejected candidates must not
  // be sitting open in every row.
  expect(screen.queryByText(/akash@vahan\.co/)).not.toBeInTheDocument()
  await userEvent.click(summary)
  expect(await screen.findByText(/akash@vahan\.co/)).toBeVisible()
  expect(screen.getByText(/risky/)).toBeVisible()
})

it('submits a hand-typed address', async () => {
  const setEmail = mockSetEmail()
  mockManual([manualItem({ outreach_id: 1, founder_name: 'Akash Gupta' })])
  render(<ManualEmail />)

  const input = await screen.findByLabelText(/address for akash gupta/i)
  await userEvent.type(input, 'akash@newdomain.com')
  await userEvent.click(screen.getByRole('button', { name: /^submit$/i }))

  expect(setEmail).toHaveBeenCalledWith(1, { address: 'akash@newdomain.com' })
})

it('shows the published-email hint when resolve found one', async () => {
  mockManual([
    manualItem({
      outreach_id: 1,
      founder_name: 'Akash Gupta',
      published_hint: 'akash@vahan.co (from company site)',
    }),
  ])
  render(<ManualEmail />)
  expect(await screen.findByText(/akash@vahan\.co \(from company site\)/)).toBeInTheDocument()
})

it('renders no published-hint markup when resolve found nothing', async () => {
  mockManual([manualItem({ outreach_id: 1, founder_name: 'Akash Gupta', published_hint: null })])
  const { container } = render(<ManualEmail />)
  await screen.findByText('Akash Gupta')
  // the absent-hint branch (an em-dash placeholder), not the hint branch
  expect(screen.queryByText(/@/)).not.toBeInTheDocument()
  expect(container.textContent).toContain('—')
})

it('marks a founder unresolved on give up', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const setEmail = mockSetEmail()
  mockManual([manualItem({ outreach_id: 1, founder_name: 'Akash Gupta' })])
  render(<ManualEmail />)

  await screen.findByText('Akash Gupta')
  await userEvent.click(screen.getByRole('button', { name: /give up/i }))

  expect(setEmail).toHaveBeenCalledWith(1, { give_up: true })
})

// --- beyond the brief -------------------------------------------------------

it('a 422 on manual address keeps the typed text', async () => {
  mockSetEmailFailure(['does not look like an email address'])
  mockManual([manualItem({ outreach_id: 1, founder_name: 'Akash Gupta' })])
  render(<ManualEmail />)

  const input = await screen.findByLabelText(/address for akash gupta/i)
  await userEvent.type(input, 'not-an-email')
  await userEvent.click(screen.getByRole('button', { name: /^submit$/i }))

  expect(await screen.findByText(/does not look like an email address/i)).toBeInTheDocument()
  expect(input).toHaveValue('not-an-email')
})

it('give-up requires confirmation and does not submit when cancelled', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  const setEmail = mockSetEmail()
  mockManual([manualItem({ outreach_id: 1, founder_name: 'Akash Gupta' })])
  render(<ManualEmail />)

  await screen.findByText('Akash Gupta')
  await userEvent.click(screen.getByRole('button', { name: /give up/i }))

  expect(setEmail).not.toHaveBeenCalled()
})

it('an empty manual list shows an empty state', async () => {
  mockManual([])
  render(<ManualEmail />)
  expect(await screen.findByText(/every founder in the pipeline has an address/i)).toBeInTheDocument()
})
