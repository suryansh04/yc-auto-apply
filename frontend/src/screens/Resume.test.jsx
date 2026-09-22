// The resume page: the one file every founder receives. It began as a
// card on the Approved screen and moved here - it is not about any one
// letter, and it has enough to say about itself to be worth a page.
import { expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithQuery } from '../test/render'
import { mockReplaceResume, mockResumeStatus } from '../test/mocks'
import Resume from './Resume'

const render = () => renderWithQuery(<Resume />)

it('shows the file itself, not a link to it', async () => {
  // "View the resume" means looking at it. A link that opens another tab
  // is one more click before the thing the page exists for.
  mockResumeStatus({ filename: 'My_Resume.pdf', size_bytes: 164089 })
  render()
  const frame = await screen.findByTitle('My resume')
  expect(frame).toHaveAttribute('src', '/api/resume')
})

it('reports the file and when it last changed', async () => {
  mockResumeStatus({ filename: 'My_Resume.pdf', size_bytes: 164089 })
  render()
  expect(await screen.findByText('My_Resume.pdf')).toBeInTheDocument()
  expect(screen.getByText('160 KB')).toBeInTheDocument()
  expect(screen.getByText('Last changed')).toBeInTheDocument()
})

it('says the claims come from parse-resume, not from this file', async () => {
  // The consequence nobody would guess: replacing the PDF does not
  // reparse it, so what a draft may say about the sender is unchanged.
  mockResumeStatus({ claims: 33 })
  render()
  expect(await screen.findByText(/33 claims parsed/i)).toBeInTheDocument()
  expect(screen.getByText(/does not re-read it/i)).toBeInTheDocument()
})

it('still offers a tab for anyone who would rather have one', async () => {
  mockResumeStatus()
  render()
  const link = await screen.findByRole('link', { name: /open in a tab/i })
  expect(link).toHaveAttribute('href', '/api/resume')
  expect(link).toHaveAttribute('target', '_blank')
})

it('replaces the resume and warns that the claims are still the old ones', async () => {
  mockResumeStatus({ claims: 33 })
  const replace = mockReplaceResume()
  render()
  await screen.findByTitle('My resume')
  await userEvent.upload(
    screen.getByLabelText(/replace the resume/i),
    new File(['%PDF-1.4 x'], 'new.pdf', { type: 'application/pdf' }),
  )
  await waitFor(() => expect(replace).toHaveBeenCalled())
  expect(await screen.findByText(/resume replaced/i)).toBeInTheDocument()
  // The command is named in the banner and again in the sidebar card;
  // the banner's copy is the one that has to appear on a replace.
  expect(screen.getByText(/every letter from now on carries the new file/i))
    .toBeInTheDocument()
  expect(screen.getAllByText(/parse-resume/).length).toBeGreaterThanOrEqual(1)
})

it('shows why a rejected upload was rejected', async () => {
  mockResumeStatus()
  mockReplaceResume({ status: 400, detail: 'that is not a PDF' })
  render()
  await screen.findByTitle('My resume')
  // Named .pdf so the input's own `accept` filter lets it through -
  // userEvent honours that, and the point here is the SERVER's refusal.
  await userEvent.upload(
    screen.getByLabelText(/replace the resume/i),
    new File(['PK zip pretending'], 'cv.pdf', { type: 'application/pdf' }),
  )
  expect(await screen.findByText(/that is not a PDF/i)).toBeInTheDocument()
})

it('says nothing can be sent when there is no resume', async () => {
  mockResumeStatus({ configured: false, filename: null, size_bytes: null })
  render()
  expect(await screen.findByText(/no resume yet/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /upload a resume/i })).toBeInTheDocument()
  expect(screen.queryByTitle('My resume')).not.toBeInTheDocument()
})
