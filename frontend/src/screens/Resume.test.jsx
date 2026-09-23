// The resume page: the one file every founder receives. It began as a
// card on the Approved screen and moved here - it is not about any one
// letter, and it has enough to say about itself to be worth a page.
import { expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithQuery } from '../test/render'
import {
  mockParseResume,
  mockParseResumeFailure,
  mockReplaceResume,
  mockResumeStatus,
} from '../test/mocks'
import Resume from './Resume'

const render = () => renderWithQuery(<Resume />)

it('shows the file itself, not a link to it', async () => {
  // "View the resume" means looking at it. A link that opens another tab
  // is one more click before the thing the page exists for.
  mockResumeStatus({ filename: 'My_Resume.pdf', size_bytes: 164089 })
  render()
  const frame = await screen.findByTitle('My resume')
  expect(frame.getAttribute('src')).toMatch(/^\/api\/resume\?/)
})

it('asks for the resume by the time it last changed', async () => {
  // Live finding (2026-09-23): the operator replaced the resume and kept
  // seeing the old one. One URL serves whatever is at RESUME_PATH, and
  // remounting an iframe on that same URL lets the browser answer from
  // its cache. The timestamp makes a replacement a different URL.
  mockResumeStatus({ modified_at: '2026-09-23T10:11:12Z' })
  render()
  const frame = await screen.findByTitle('My resume')
  expect(frame).toHaveAttribute('src', '/api/resume?v=2026-09-23T10%3A11%3A12Z')
  expect(screen.getByRole('link', { name: /open in a tab/i }))
    .toHaveAttribute('href', '/api/resume?v=2026-09-23T10%3A11%3A12Z')
})

it('reports the file and when it last changed', async () => {
  mockResumeStatus({ filename: 'My_Resume.pdf', size_bytes: 164089 })
  render()
  expect(await screen.findByText('My_Resume.pdf')).toBeInTheDocument()
  expect(screen.getByText('160 KB')).toBeInTheDocument()
  expect(screen.getByText('Last changed')).toBeInTheDocument()
})

it('says what a draft is allowed to claim about you', async () => {
  mockResumeStatus({ claims: 33 })
  render()
  expect(await screen.findByText(/33 claims read from this resume/i)).toBeInTheDocument()
})

it('still offers a tab for anyone who would rather have one', async () => {
  mockResumeStatus()
  render()
  const link = await screen.findByRole('link', { name: /open in a tab/i })
  expect(link.getAttribute('href')).toMatch(/^\/api\/resume\?/)
  expect(link).toHaveAttribute('target', '_blank')
})

it('reads the resume it just uploaded, without being asked twice', async () => {
  // Operator request (2026-09-23): the parse follows the upload. A new
  // PDF beside the old claims is a trap with no upside, and the button
  // that used to ask for it is gone.
  mockResumeStatus({ claims: 33 })
  const replace = mockReplaceResume({ claims: 7 })
  render()
  await screen.findByTitle('My resume')
  await userEvent.upload(
    screen.getByLabelText(/replace the resume/i),
    new File(['%PDF-1.4 x'], 'new.pdf', { type: 'application/pdf' }),
  )
  await waitFor(() => expect(replace).toHaveBeenCalled())
  expect(await screen.findByText(/resume replaced and read/i)).toBeInTheDocument()
  expect(screen.getByText(/7 claims a letter may now make about you/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /re-read my resume/i })).not.toBeInTheDocument()
})

it('offers a way back in when the file saved but could not be read', async () => {
  // The upload itself succeeded, so this is a report, not a failure -
  // and the claims are still the previous ones, which has to be said.
  mockResumeStatus({ claims: 33 })
  mockReplaceResume({ claims: 0, parse_error: 'the resume parser failed (RuntimeError)' })
  render()
  await screen.findByTitle('My resume')
  await userEvent.upload(
    screen.getByLabelText(/replace the resume/i),
    new File(['%PDF-1.4 x'], 'new.pdf', { type: 'application/pdf' }),
  )
  expect(await screen.findByText(/could not read it/i)).toBeInTheDocument()
  expect(screen.getByText(/RuntimeError/)).toBeInTheDocument()

  const parse = mockParseResume({ claims: 5 })
  await userEvent.click(screen.getByRole('button', { name: /try reading it again/i }))
  await waitFor(() => expect(parse).toHaveBeenCalled())
  expect(await screen.findByText(/5 claims a letter may now make about you/i)).toBeInTheDocument()
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


// --- reading the resume from here (operator request 2026-09-23) -----------
//
// Parsing used to be `python -m app.cli parse-resume PATH` and nothing
// else, which made a brand-new operator's first Draft a dead end: it
// fails until the resume has been read, and the UI had nowhere to read
// it from.

async function uploadOne() {
  await screen.findByTitle('My resume')
  await userEvent.upload(
    screen.getByLabelText(/replace the resume/i),
    new File(['%PDF-1.4 x'], 'new.pdf', { type: 'application/pdf' }),
  )
}

it('does not let a partial parse look like a clean one', async () => {
  // "it worked" and "it worked and threw half of it away" must not read
  // the same.
  mockResumeStatus({ claims: 3 })
  mockReplaceResume({ claims: 2, dropped: 4, unverified: 1 })
  render()
  await uploadOne()
  expect(await screen.findByText(/4 were thrown away/i)).toBeInTheDocument()
  expect(screen.getByText(/1 could not be matched/i)).toBeInTheDocument()
})

it('says why a retry failed rather than leaving the count unchanged in silence', async () => {
  mockResumeStatus({ claims: 3 })
  mockReplaceResume({ claims: 0, parse_error: 'could not read that file (PdfReadError)' })
  mockParseResumeFailure(422, 'parser produced no usable claims; existing profile not replaced')
  render()
  await uploadOne()
  await userEvent.click(await screen.findByRole('button', { name: /try reading it again/i }))
  expect(await screen.findByText(/no usable claims/i)).toBeInTheDocument()
})

it('offers nothing to re-read when there is no resume', async () => {
  mockResumeStatus({ configured: false })
  render()
  await screen.findByText(/no resume yet/i)
  expect(screen.queryByRole('button', { name: /re-read my resume/i })).not.toBeInTheDocument()
})
