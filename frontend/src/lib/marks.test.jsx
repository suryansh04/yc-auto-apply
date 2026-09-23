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


// --- the company paragraph (operator request 2026-09-23) -------------------
//
// "no highlight that section to". The generic-line button had to be taken
// on trust; marking the paragraph is what makes the swap show its work.

const INSIGHT = {
  kind: 'insight',
  message: 'This is the paragraph about them - the only part the letter researches.',
  quote: 'You built a voice layer.',
  generic: 'I came across Vahan while looking for teams.',
}

it('marks the company paragraph in its own colour, not the checks\u2019', async () => {
  show('One thing. You built a voice layer. Another.', [INSIGHT])
  const mark = screen.getByLabelText(/paragraph about them/i)
  expect(mark.tagName).toBe('MARK')
  expect(mark).toHaveAttribute('data-kind', 'insight')
  // Sky, never amber: amber means "something here is wrong", and this
  // mark makes no such claim.
  expect(mark.className).toMatch(/sky/)
  expect(mark.className).not.toMatch(/amber/)
})

it('hovering the company paragraph offers the generic line', async () => {
  show('One thing. You built a voice layer. Another.', [INSIGHT])
  await hover(screen.getByLabelText(/paragraph about them/i))
  expect(await screen.findByText('I came across Vahan while looking for teams.')).toBeInTheDocument()
})

it('the swap is offered from the paragraph itself', async () => {
  const onUseGeneric = vi.fn()
  show('One thing. You built a voice layer. Another.', [{ ...INSIGHT, onUseGeneric }])
  await hover(screen.getByLabelText(/paragraph about them/i))
  await userEvent.click(await screen.findByRole('tab', { name: /generic/i }))
  await userEvent.click(await screen.findByRole('button', { name: /use a generic line/i }))
  expect(onUseGeneric).toHaveBeenCalled()
})

it('a check mark wins where the two overlap', async () => {
  // They overlap by design - a hook note IS about that paragraph - and a
  // mark inside a mark renders as neither. Something wrong with the
  // words is the more urgent of the two claims.
  const { container } = show('One thing. You built a voice layer. Another.', [
    { message: 'hook shares no distinctive term', quote: 'You built a voice layer.' },
    INSIGHT,
  ])
  const marks = container.querySelectorAll('mark')
  expect(marks).toHaveLength(1)
  expect(marks[0]).toHaveAttribute('data-kind', 'check')
})

it('both are marked when they do not overlap', async () => {
  const { container } = show('A semicolon; here. You built a voice layer.', [
    { message: 'contains a semicolon', quote: ';' },
    INSIGHT,
  ])
  const kinds = [...container.querySelectorAll('mark')].map((m) => m.dataset.kind)
  expect(kinds).toEqual(['check', 'insight'])
})


it('the company paragraph offers an AI rewrite and the generic line as two tabs', async () => {
  // "give two tabs one for ai generated suggestion, other one should be
  // generic" - the two answer different questions ("can this be said
  // better?" and "can this be said at all?"), so neither is a step on
  // the way to the other.
  show('One thing. You built a voice layer. Another.', [INSIGHT], {
    suggestions: {
      0: { index: 0, quote: 'You built a voice layer.',
           replacement: 'You put hiring on a phone call.', why: 'names the choice' },
    },
  })
  await hover(screen.getByLabelText(/paragraph about them/i))
  // The AI tab opens first when there is a rewrite to show.
  expect(await screen.findByText('You put hiring on a phone call.')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: /generic/i }))
  expect(await screen.findByText('I came across Vahan while looking for teams.')).toBeInTheDocument()
})

it('opens on the generic tab when there is no AI rewrite yet', async () => {
  show('One thing. You built a voice layer. Another.', [INSIGHT])
  await hover(screen.getByLabelText(/paragraph about them/i))
  expect(await screen.findByText('I came across Vahan while looking for teams.')).toBeInTheDocument()
})

it('says so instead of offering a swap that would change nothing', async () => {
  // The paragraph IS the generic line already - pressing the button
  // would replace it with itself.
  show('One thing. I came across Vahan while looking for teams. Another.', [
    { ...INSIGHT, quote: 'I came across Vahan while looking for teams.' },
  ], {})
  await hover(screen.getByLabelText(/paragraph about them/i))
  expect(await screen.findByText(/already using it/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /use a generic line/i })).not.toBeInTheDocument()
})
