// The line about the founder (operator request 2026-09-25).
import { expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FounderLine from './FounderLine'
import { renderWithQuery } from '../test/render'
import { mockUseFounderLine, mockWriteFounderLine } from '../test/mocks'

const LINE = 'I found your ML background really interesting.'

it('writes lines about the founder and shows what they were written from', async () => {
  const write = mockWriteFounderLine({
    options: [{ style: 'overlap', line: LINE }],
    material: 'Studied ML at IIT Madras.',
  })
  renderWithQuery(<FounderLine outreachId={4} founderName="Harsh Vardhan" currentLine={null} />)

  await userEvent.click(screen.getByRole('button', { name: /write a line about harsh/i }))

  expect(await screen.findByText(LINE)).toBeInTheDocument()
  expect(screen.getByText('Overlap with you')).toBeInTheDocument()
  expect(screen.getByText(/written from their yc bio/i)).toBeInTheDocument()
  expect(write).toHaveBeenCalledWith(4, { avoid: [] })
})

it('puts the chosen line into the letter', async () => {
  mockWriteFounderLine({ options: [{ style: 'short', line: LINE }] })
  const use = mockUseFounderLine()
  renderWithQuery(<FounderLine outreachId={4} founderName="Harsh" currentLine={null} />)

  await userEvent.click(screen.getByRole('button', { name: /write a line/i }))
  await userEvent.click(await screen.findByRole('button', { name: `Use the line: ${LINE}` }))

  expect(use).toHaveBeenCalledWith(4, { line: LINE })
})

it('removes the line the letter has', async () => {
  const use = mockUseFounderLine()
  renderWithQuery(<FounderLine outreachId={4} founderName="Harsh" currentLine={LINE} />)

  expect(screen.getByText(LINE)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /remove it/i }))

  await expect.poll(() => use.mock.calls.length).toBe(1)
  expect(use).toHaveBeenCalledWith(4, { line: '' })
})

it('says why there is nothing to offer', async () => {
  mockWriteFounderLine({ unavailable: 'their YC bio says nothing about them' })
  renderWithQuery(<FounderLine outreachId={4} founderName="Harsh" currentLine={null} />)

  await userEvent.click(screen.getByRole('button', { name: /write a line/i }))

  expect(await screen.findByText(/says nothing about them/i)).toBeInTheDocument()
})
