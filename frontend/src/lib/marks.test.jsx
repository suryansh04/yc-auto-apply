// Wrapping the words a check objects to (operator request 2026-09-22).
import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { markUp } from './marks'

const show = (text, highlights, opts) => render(<p>{markUp(text, highlights, opts)}</p>)

it('marks the quoted words and nothing else', () => {
  show('One thing; another thing.', [{ message: 'contains a semicolon', quote: ';' }])
  const mark = screen.getByTitle('contains a semicolon')
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
  await userEvent.click(screen.getByTitle('contains a semicolon'))
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
