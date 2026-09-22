// Wrapping the words a check objects to (operator request 2026-09-22).
import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { markUp } from './marks'

const show = (text, highlights, opts) => render(<p>{markUp(text, highlights, opts)}</p>)

it('marks the quoted words and nothing else', () => {
  show('One thing; another thing.', [{ message: 'contains a semicolon', quote: ';' }])
  const mark = screen.getByLabelText('contains a semicolon')
  expect(mark.tagName).toBe('MARK')
  expect(mark).toHaveTextContent(';')
  // The rest of the sentence is still there, unwrapped.
  expect(screen.getByText(/One thing/)).toBeInTheDocument()
})

it('leaves the text alone when there is nothing to mark', () => {
  const { container } = show('A clean letter.', [])
  expect(container.querySelectorAll('mark')).toHaveLength(0)
  expect(container).toHaveTextContent('A clean letter.')
})

it('ignores a note with no quote', () => {
  const { container } = show('A clean letter.', [{ message: 'body exceeds 380 words', quote: null }])
  expect(container.querySelectorAll('mark')).toHaveLength(0)
})

it('skips a quote that is not in the text rather than guessing', () => {
  // Marking the wrong words would be worse than marking none.
  const { container } = show('A clean letter.', [{ message: 'x', quote: 'not present' }])
  expect(container.querySelectorAll('mark')).toHaveLength(0)
  expect(container).toHaveTextContent('A clean letter.')
})

it('marks several places at once, in order', () => {
  const { container } = show('One; two. Three [4].', [
    { message: 'semicolon', quote: ';' },
    { message: 'citation', quote: '[4]' },
  ])
  const marks = [...container.querySelectorAll('mark')]
  expect(marks.map((m) => m.textContent)).toEqual([';', '[4]'])
})

it('never nests one mark inside another', () => {
  const { container } = show('I built a scheduler in Python.', [
    { message: 'sentence', quote: 'I built a scheduler in Python.' },
    { message: 'word', quote: 'Python' },
  ])
  const marks = [...container.querySelectorAll('mark')]
  expect(marks).toHaveLength(1)
  expect(marks[0].querySelector('mark')).toBeNull()
})

it('tells the panel which note was clicked', async () => {
  const onSelect = vi.fn()
  show('One thing; another.', [{ message: 'contains a semicolon', quote: ';' }], { onSelect })
  await userEvent.click(screen.getByLabelText('contains a semicolon'))
  expect(onSelect).toHaveBeenCalledWith(0)
})

it('shows the active mark differently from the rest', () => {
  const { container } = show('One; two [4].', [
    { message: 'a', quote: ';' },
    { message: 'b', quote: '[4]' },
  ], { activeIndex: 1 })
  const [first, second] = container.querySelectorAll('mark')
  expect(second.className).not.toBe(first.className)
  expect(second.className).toContain('underline')
})

it('keeps the whole text when the quote is the whole text', () => {
  const { container } = show('All of it.', [{ message: 'x', quote: 'All of it.' }])
  expect(container).toHaveTextContent('All of it.')
  expect(container.querySelectorAll('mark')).toHaveLength(1)
})


// --- the hover card (operator request 2026-09-23) ---------------------------
//
// "i want when i hover over it, it shows suggestion just like grammarly
// use sarvam for suggestion". The note says what is wrong; the rewrite
// says what to write instead. The rewrite arrives after the highlight and
// may never arrive at all, so all three states are tested.

const SEMICOLON = [{ message: 'contains a semicolon', quote: ';' }]

async function hover(mark) {
  await userEvent.hover(mark)
  // Radix opens on a delay; findBy* below does the waiting.
}

it('hovering a mark shows the note', async () => {
  show('One thing; another.', SEMICOLON)
  await hover(screen.getByLabelText('contains a semicolon'))
  expect(await screen.findByText('contains a semicolon')).toBeInTheDocument()
})

it('hovering shows the rewrite and why, when one came back', async () => {
  show('One thing; another.', SEMICOLON, {
    suggestions: { 0: { index: 0, quote: ';', replacement: '. ', why: 'a full stop reads plainer' } },
  })
  await hover(screen.getByLabelText('contains a semicolon'))
  expect(await screen.findByText('a full stop reads plainer')).toBeInTheDocument()
})

it('says it is still thinking while the rewrite is in flight', async () => {
  show('One thing; another.', SEMICOLON, { pending: true })
  await hover(screen.getByLabelText('contains a semicolon'))
  expect(await screen.findByText(/looking for a better line/i)).toBeInTheDocument()
})

it('says so plainly when there is no rewrite for a note', async () => {
  // An unconfigured key, or a model with nothing better to offer. The
  // card must still read as finished rather than as broken.
  show('One thing; another.', SEMICOLON, { pending: false })
  await hover(screen.getByLabelText('contains a semicolon'))
  expect(await screen.findByText(/no rewrite for this one/i)).toBeInTheDocument()
})

it('an empty replacement is offered as a deletion, not as blank text', async () => {
  show('One thing; another.', SEMICOLON, {
    suggestions: { 0: { index: 0, quote: ';', replacement: '', why: 'it does no work' } },
    onApply: vi.fn(),
  })
  await hover(screen.getByLabelText('contains a semicolon'))
  expect(await screen.findByText(/cut this entirely/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /cut it/i })).toBeInTheDocument()
})

it('accepting a rewrite reports which note it belongs to', async () => {
  const onApply = vi.fn()
  show('One thing; another.', SEMICOLON, {
    suggestions: { 0: { index: 0, quote: ';', replacement: '. ', why: 'plainer' } },
    onApply,
  })
  await hover(screen.getByLabelText('contains a semicolon'))
  await userEvent.click(await screen.findByRole('button', { name: /use this/i }))
  expect(onApply).toHaveBeenCalledWith(0)
})

it('a rewrite is keyed to its own note, never to the first mark', async () => {
  // The suggestion map is keyed by position in `highlights`, so a rewrite
  // for the second note cannot surface under the first.
  show('One thing; another. Too long.', [
    { message: 'contains a semicolon', quote: ';' },
    { message: 'a sentence exceeds 45 words', quote: 'Too long.' },
  ], {
    suggestions: { 1: { index: 1, quote: 'Too long.', replacement: 'Short.', why: 'trimmed' } },
  })
  await hover(screen.getByLabelText('contains a semicolon'))
  expect(await screen.findByText(/no rewrite for this one/i)).toBeInTheDocument()
})
