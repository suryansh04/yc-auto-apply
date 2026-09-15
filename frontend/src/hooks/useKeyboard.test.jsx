// Task 16: useKeyboard in isolation, per the brief - the input-focus guard
// ("bindings must be inert while any text input or textarea has focus") is
// the one piece of behaviour the coordinator singled out as needing a test
// that fails when the guard is deleted, so it gets dedicated coverage here
// rather than only being exercised indirectly through ReviewQueue.test.jsx.
import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useKeyboard } from './useKeyboard'

function Harness({ bindings, enabled }) {
  useKeyboard(bindings, { enabled })
  return (
    <div>
      <input aria-label="typing target" />
      <textarea aria-label="body target" />
    </div>
  )
}

it('calls the bound handler on a matching keydown', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  await userEvent.keyboard('a')
  expect(onA).toHaveBeenCalledTimes(1)
})

it('does nothing for an unbound key', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  await userEvent.keyboard('z')
  expect(onA).not.toHaveBeenCalled()
})

// --- the guard --------------------------------------------------------

it('ignores keys while a text input has focus', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  await userEvent.click(screen.getByLabelText('typing target'))
  await userEvent.keyboard('a')
  expect(onA).not.toHaveBeenCalled()
})

it('ignores keys while a textarea has focus', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  await userEvent.click(screen.getByLabelText('body target'))
  await userEvent.keyboard('a')
  expect(onA).not.toHaveBeenCalled()
})

it('resumes handling keys once focus leaves the input', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  const input = screen.getByLabelText('typing target')
  await userEvent.click(input)
  input.blur()
  await userEvent.keyboard('a')
  expect(onA).toHaveBeenCalledTimes(1)
})

// --- enabled flag -------------------------------------------------------

it('suspends all bindings when enabled is false', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} enabled={false} />)
  await userEvent.keyboard('a')
  expect(onA).not.toHaveBeenCalled()
})

it('picks up new handlers without needing to re-register the listener', async () => {
  const onA1 = vi.fn()
  const onA2 = vi.fn()
  const { rerender } = render(<Harness bindings={{ a: onA1 }} />)
  rerender(<Harness bindings={{ a: onA2 }} />)
  await userEvent.keyboard('a')
  expect(onA1).not.toHaveBeenCalled()
  expect(onA2).toHaveBeenCalledTimes(1)
})
