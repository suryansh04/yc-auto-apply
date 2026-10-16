// What the letter was written from, with its sources (2026-09-25).
import { expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import ResearchFacts from './ResearchFacts'

it('shows each fact with a link to its source, and marks old ones', () => {
  render(<ResearchFacts research={{
    facts: [
      { kind: 'specific_thing', text: 'Aadi, pretrained on 49 plant genomes', label: 'Hugging Face',
        url: 'https://huggingface.co/x', old: false },
      { kind: 'founder_fact', text: 'Talked to drivers before starting', label: 'news', url: 'https://n.test', old: true },
    ],
    question: 'How do you clean phenotype data?',
    note: 'No real detail about the founder was found.',
  }} />)
  expect(screen.getByRole('link', { name: /hugging face/i })).toHaveAttribute('href', 'https://huggingface.co/x')
  expect(screen.getByText('OLD')).toBeInTheDocument()
  expect(screen.getByText(/open it before sending/i)).toBeInTheDocument()
  expect(screen.getByText('How do you clean phenotype data?')).toBeInTheDocument()
})
