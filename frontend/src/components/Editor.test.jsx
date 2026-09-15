// Fix round 2 (N03: the review's mutation testing found that removing
// ReviewQueue.jsx's `key={current.outreach_id}` on <Editor> survives the
// full suite - the guard was redundant behind T16-3's key-suspension
// while editing, but unpinned). This is a direct, component-level test
// of the thing the `key` prop actually buys: switching `key` forces
// React to unmount the old instance and mount a fresh one, so any
// unsaved typed text can never leak from one draft's editor to
// another's, regardless of how the two are ever reached (today, only by
// closing and reopening the editor; the `key` is defence-in-depth
// against a future mouse-driven "switch while editing" path).
import { expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Editor from './Editor'

it('a different key mounts a fresh Editor instance, discarding unsaved typed text', async () => {
  const { rerender } = render(
    <Editor
      key={1}
      initialSubject="Subject 1"
      initialBody="Body 1"
      errors={[]}
      onSave={() => {}}
      onCancel={() => {}}
    />,
  )
  const box = screen.getByRole('textbox', { name: /body/i })
  await userEvent.clear(box)
  await userEvent.type(box, 'unsaved draft text for item 1')
  expect(box).toHaveValue('unsaved draft text for item 1')

  rerender(
    <Editor
      key={2}
      initialSubject="Subject 2"
      initialBody="Body 2"
      errors={[]}
      onSave={() => {}}
      onCancel={() => {}}
    />,
  )

  // A fresh instance for item 2 - the unsaved text from item 1 is gone,
  // never carried over onto a different draft.
  expect(screen.getByRole('textbox', { name: /body/i })).toHaveValue('Body 2')
})

it('the same key across rerenders keeps the same instance (typed text survives an unrelated prop change)', async () => {
  const { rerender } = render(
    <Editor
      key={1}
      initialSubject="Subject 1"
      initialBody="Body 1"
      errors={[]}
      onSave={() => {}}
      onCancel={() => {}}
    />,
  )
  const box = screen.getByRole('textbox', { name: /body/i })
  await userEvent.clear(box)
  await userEvent.type(box, 'still typing')

  // Same key, only `errors` changes (the normal 422-rejection re-render).
  rerender(
    <Editor
      key={1}
      initialSubject="Subject 1"
      initialBody="Body 1"
      errors={['some validator message']}
      onSave={() => {}}
      onCancel={() => {}}
    />,
  )

  expect(screen.getByRole('textbox', { name: /body/i })).toHaveValue('still typing')
})
