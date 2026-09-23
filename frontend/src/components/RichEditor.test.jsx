// The formatting editor (operator request 2026-09-23): "give option such
// as bolding, italic, increasing size of a particular text, numerical or
// bullet points". Every control here must produce markup the server keeps
// (app/domain/rich_text.py) - a button whose effect vanishes on save is
// worse than no button.
import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RichEditor from './RichEditor'

function setup(html = '<p>Hello world</p>') {
  const onChange = vi.fn()
  render(<RichEditor initialHtml={html} onChange={onChange} />)
  const last = () => onChange.mock.calls.at(-1)?.[0]
  return { onChange, last }
}

async function selectAll() {
  await userEvent.click(await screen.findByRole('textbox', { name: 'Body' }))
  await userEvent.keyboard('{Control>}a{/Control}')
}

it('starts from the letter it is given', async () => {
  setup('<p>This will take 45 seconds.</p>')
  expect(await screen.findByRole('textbox', { name: 'Body' }))
    .toHaveTextContent('This will take 45 seconds.')
})

it('offers exactly the formatting the server keeps', async () => {
  setup()
  const toolbar = await screen.findByRole('toolbar', { name: /formatting/i })
  for (const name of ['Bold', 'Italic', 'Underline', 'Bulleted list', 'Numbered list', 'Undo', 'Redo']) {
    expect(screen.getByRole('button', { name })).toBeInTheDocument()
  }
  expect(screen.getByRole('combobox', { name: /text size/i })).toBeInTheDocument()
  expect(toolbar).toBeInTheDocument()
})

it('bolds, italicises and underlines the selection', async () => {
  const { last } = setup()
  await selectAll()
  await userEvent.click(screen.getByRole('button', { name: 'Bold' }))
  expect(last()).toBe('<p><strong>Hello world</strong></p>')
  await userEvent.click(screen.getByRole('button', { name: 'Italic' }))
  await userEvent.click(screen.getByRole('button', { name: 'Underline' }))
  expect(last()).toContain('<em>')
  expect(last()).toContain('<u>')
})

it('says which formatting is on where the cursor is', async () => {
  setup('<p><strong>Hello</strong></p>')
  await selectAll()
  expect(screen.getByRole('button', { name: 'Bold' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Italic' })).toHaveAttribute('aria-pressed', 'false')
})

it('turns paragraphs into bulleted and numbered lists', async () => {
  const { last } = setup('<p>one</p>')
  await selectAll()
  await userEvent.click(screen.getByRole('button', { name: 'Bulleted list' }))
  expect(last()).toContain('<ul>')
  await userEvent.click(screen.getByRole('button', { name: 'Numbered list' }))
  expect(last()).toContain('<ol>')
})

it('typing keeps working after formatting', async () => {
  const { last } = setup()
  await selectAll()
  await userEvent.click(screen.getByRole('button', { name: 'Bold' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'Body' }), ' more')
  expect(last()).toBe('<p><strong>Hello world more</strong></p>')
})

it('keeps text size it is given', async () => {
  // Sizes come in from a saved letter as a span the server allowed.
  setup('<p><span style="font-size: 20px">Big</span> small</p>')
  const box = await screen.findByRole('textbox', { name: 'Body' })
  expect(box.querySelector('span[style*="font-size"]')).toHaveTextContent('Big')
})

it('offers no control the server would strip', async () => {
  // Headings, quotes, code and links are switched off rather than left
  // in to vanish on save.
  setup()
  await screen.findByRole('toolbar')
  for (const name of [/heading/i, /quote/i, /code/i, /link/i, /strike/i]) {
    expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
  }
})
