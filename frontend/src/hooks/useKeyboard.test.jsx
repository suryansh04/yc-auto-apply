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
      <select aria-label="select target">
        <option value="a">a</option>
      </select>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <div aria-label="editable target" contentEditable="true" tabIndex={0} suppressContentEditableWarning />
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

it('ignores keys while a select has focus', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  await userEvent.click(screen.getByLabelText('select target'))
  await userEvent.keyboard('a')
  expect(onA).not.toHaveBeenCalled()
})

it('ignores keys while a contentEditable element has focus', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  screen.getByLabelText('editable target').focus()
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

// --- bypassFocusGuard (fix round 1, F6: Escape must cancel the editor
// even with focus still inside its body textarea) --------------------

it('a plain function binding is still guarded by focus (bypassFocusGuard defaults to false)', async () => {
  const onA = vi.fn()
  render(<Harness bindings={{ a: onA }} />)
  await userEvent.click(screen.getByLabelText('typing target'))
  await userEvent.keyboard('a')
  expect(onA).not.toHaveBeenCalled()
})

it('a { handler, bypassFocusGuard: true } binding fires even with a text input focused', async () => {
  const onEscape = vi.fn()
  render(<Harness bindings={{ Escape: { handler: onEscape, bypassFocusGuard: true } }} />)
  await userEvent.click(screen.getByLabelText('typing target'))
  await userEvent.keyboard('{Escape}')
  expect(onEscape).toHaveBeenCalledTimes(1)
})

it('a { handler, bypassFocusGuard: true } binding fires even with a textarea focused', async () => {
  const onEscape = vi.fn()
  render(<Harness bindings={{ Escape: { handler: onEscape, bypassFocusGuard: true } }} />)
  await userEvent.click(screen.getByLabelText('body target'))
  await userEvent.keyboard('{Escape}')
  expect(onEscape).toHaveBeenCalledTimes(1)
})

it('a bypassFocusGuard binding still respects the enabled flag', async () => {
  const onEscape = vi.fn()
  render(
    <Harness
      bindings={{ Escape: { handler: onEscape, bypassFocusGuard: true } }}
      enabled={false}
    />,
  )
  await userEvent.keyboard('{Escape}')
  expect(onEscape).not.toHaveBeenCalled()
})
