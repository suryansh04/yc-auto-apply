// Task 16's Review Queue screen (+ fix rounds 1 and 2, coordinator review
// task-16-review.md). server's listen/resetHandlers/close lifecycle is
// owned globally by src/test/setup.js (afterEach(cleanup) too, per
// task-15-report.md's finding) - this file only registers handlers via
// src/test/mocks.js helpers.
//
// A note on timing: several tests below `await` some piece of rendered
// text before pressing a key. This is required, not decorative - fix
// round 1's F2 finding established that the screen must act only on the
// item it has actually rendered (never on a second, "read the cache
// directly" source of truth), so a key pressed before the first render
// with data has committed is correctly a no-op. The coordinator's review
// approved this one-line change to the original brief tests.
import { expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithQuery as render } from '../test/render'
import {
  item,
  mockApprove,
  mockApproveFailure,
  mockEdit,
  mockEditFailure,
  mockQueue,
  mockQueueSequence,
  mockRedraft,
  mockCheckDraft,
  mockSuggestFixes,
  mockSuggestFailure,
  mockGenericLine,
  mockGenericLineFailure,
} from '../test/mocks'
import ReviewQueue from './ReviewQueue'

// --- the brief's nine ---------------------------------------------------

it('shows the composed email, not the bare stored body', async () => {
  mockQueue([item({ body: 'Body text.', wire_text: 'Hi Akash,\n\nBody text.\n\nSuryansh' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/Hi Akash,/)).toBeInTheDocument()
})

it('shows the cited source beside the draft so a claim is checkable', async () => {
  mockQueue([item({ source_kind: 'news', source_text: 'OpenAI backs Vahan' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/OpenAI backs Vahan/)).toBeInTheDocument()
})

it('approves on "a" and advances to the next draft', async () => {
  const approve = mockApprove()
  mockQueue([item({ outreach_id: 1 }), item({ outreach_id: 2, company_name: 'Second Co' })])
  render(<ReviewQueue />)
  await screen.findByText(/1 of 2/)
  await userEvent.keyboard('a')
  expect(approve).toHaveBeenCalledWith(1)
  expect(await screen.findByText(/Second Co/)).toBeInTheDocument()
})

it('rolls back the optimistic advance when approve fails', async () => {
  mockApproveFailure()
  mockQueue([item({ outreach_id: 1, company_name: 'First Co' })])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/First Co/)).toBeInTheDocument()
  expect(await screen.findByText(/could not approve/i)).toBeInTheDocument()
})

it('shows validator errors verbatim when an edit is rejected', async () => {
  mockEditFailure(['contains a semicolon', 'no explicit internship ask'])
  mockQueue([item({})])
  render(<ReviewQueue />)
  await screen.findByText('Review queue')
  await userEvent.keyboard('e')
  await userEvent.click(await screen.findByRole('button', { name: /^save/i }))
  expect(await screen.findByText('contains a semicolon')).toBeInTheDocument()
  expect(screen.getByText('no explicit internship ask')).toBeInTheDocument()
})

it('keeps the typed text when an edit is rejected', async () => {
  mockEditFailure(['contains a semicolon'])
  mockQueue([item({})])
  render(<ReviewQueue />)
  await screen.findByText('Review queue')
  await userEvent.keyboard('e')
  const box = await screen.findByRole('textbox', { name: /body/i })
  await userEvent.clear(box)
  await userEvent.type(box, 'my own words')
  await userEvent.click(screen.getByRole('button', { name: /^save/i }))
  expect(await screen.findByDisplayValue(/my own words/)).toBeInTheDocument()
})

it('shows what the checks noticed as advice, not as a refusal', async () => {
  // 2026-09-22: this used to shout "Needs a fix before approving" in red,
  // which read as a gate. Nothing ever stopped the approve, and it is the
  // operator's name on the letter, so the note is grouped as a suggestion.
  mockQueue([item({ last_error: 'cited posting is not intern-appropriate' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/ai suggestions/i)).toBeInTheDocument()
  expect(screen.getByText(/cited posting is not intern-appropriate/)).toBeInTheDocument()
  expect(screen.getByText(/you can approve anyway/i)).toBeInTheDocument()
  expect(screen.queryByText(/needs a fix before approving/i)).not.toBeInTheDocument()
  // ...and Approve is live, not disabled.
  expect(screen.getByRole('button', { name: /approve/i })).toBeEnabled()
})

it('re-checks the draft on demand and shows the fresh notes', async () => {
  mockQueue([item({ last_error: 'cited posting is not intern-appropriate' })])
  const check = mockCheckDraft({ notes: ['contains a semicolon'] })
  render(<ReviewQueue />)
  await screen.findByText(/ai suggestions/i)
  await userEvent.click(screen.getByRole('button', { name: /re-check/i }))
  expect(await screen.findByText('contains a semicolon')).toBeInTheDocument()
  expect(check).toHaveBeenCalled()
})

it('a re-check that finds nothing says so', async () => {
  mockQueue([item({ last_error: 'cited posting is not intern-appropriate' })])
  mockCheckDraft({ notes: [] })
  render(<ReviewQueue />)
  await screen.findByText(/ai suggestions/i)
  await userEvent.click(screen.getByRole('button', { name: /re-check/i }))
  expect(await screen.findByText(/nothing flagged on the last check/i)).toBeInTheDocument()
})

it('shows the critic note as one dismissible line', async () => {
  mockQueue([item({ critic_verdict: 'NEEDS_REVISION', critic_note: 'opener is generic' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/opener is generic/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
  expect(screen.queryByText(/opener is generic/)).not.toBeInTheDocument()
})

it('ignores review keys while the editor is open', async () => {
  const approve = mockApprove()
  mockQueue([item({})])
  render(<ReviewQueue />)
  await screen.findByText('Review queue')
  await userEvent.keyboard('e')
  await userEvent.type(await screen.findByRole('textbox', { name: /body/i }), 'a')
  expect(approve).not.toHaveBeenCalled()
})

// --- fix round 1: F1/F2 - identity by id, not position -----------------

it('after a real approve (refetch drops the approved row), the very next draft is shown - not skipped', async () => {
  const approve = mockApprove()
  mockQueueSequence([
    [
      item({ outreach_id: 1, company_name: 'First Co' }),
      item({ outreach_id: 2, company_name: 'Second Co' }),
      item({ outreach_id: 3, company_name: 'Third Co' }),
    ],
    // The backend's own behaviour: the approved row is gone from
    // `pending()` on the very next GET.
    [
      item({ outreach_id: 2, company_name: 'Second Co' }),
      item({ outreach_id: 3, company_name: 'Third Co' }),
    ],
  ])
  render(<ReviewQueue />)
  await screen.findByText(/1 of 3/)
  await userEvent.keyboard('a')
  expect(approve).toHaveBeenCalledWith(1)
  // Second Co, not Third Co - the shrinking refetch must not skip a draft.
  expect(await screen.findByText(/Second Co/)).toBeInTheDocument()
  expect(screen.queryByText(/Third Co/)).not.toBeInTheDocument()
  expect(await screen.findByText(/1 of 2/)).toBeInTheDocument()
})

it('rolls back to the exact item acted on, in a 3-item queue', async () => {
  mockApproveFailure(500, 'server exploded')
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
    item({ outreach_id: 3, company_name: 'Third Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/1 of 3/)
  // Move to the second item, then fail approving it.
  await userEvent.keyboard('j')
  await screen.findByText(/2 of 3/)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/server exploded/)).toBeInTheDocument()
  expect(await screen.findByText(/2 of 3/)).toBeInTheDocument()
  expect(screen.getByText(/Second Co/)).toBeInTheDocument()
})

it('acts on the rendered item, never on data the operator has not seen', async () => {
  // A pathological case for the old "read the cache directly" fix: fire
  // the key the instant render() returns, before the query has even
  // resolved. The correct behaviour is a no-op (nothing has been shown
  // yet), not approving an item the DOM never displayed.
  const approve = mockApprove()
  mockQueue([item({ outreach_id: 1, company_name: 'First Co' })])
  render(<ReviewQueue />)
  await userEvent.keyboard('a')
  expect(approve).not.toHaveBeenCalled()
  // Once the item actually renders, "a" works normally.
  await screen.findByText(/First Co/)
  await userEvent.keyboard('a')
  expect(approve).toHaveBeenCalledWith(1)
})

// --- fix round 1: F3 - keys suspended while the editor is open ---------

it('a key fired with focus on the editor\'s Save button does not approve', async () => {
  const approve = mockApprove()
  mockQueue([item({})])
  render(<ReviewQueue />)
  await screen.findByText('Review queue')
  await userEvent.keyboard('e')
  const saveButton = await screen.findByRole('button', { name: /^save/i })
  saveButton.focus()
  await userEvent.keyboard('a')
  expect(approve).not.toHaveBeenCalled()
})

it('cannot navigate to another draft with "j" while the editor is open, so Save can never carry one draft\'s text to another', async () => {
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('e')
  const cancelButton = await screen.findByRole('button', { name: /cancel/i })
  cancelButton.focus()
  await userEvent.keyboard('j')
  // Still editing draft 1 - the counter/company are hidden behind the
  // editor, but the Editor is keyed by outreach_id, so if "j" had leaked
  // through, the Save button below would be the same rendered instance.
  // The direct proof: "Second Co" never appears, because we're still on
  // item 1's editor.
  expect(await screen.findByRole('textbox', { name: /body/i })).toBeInTheDocument()
  expect(screen.queryByText(/Second Co/)).not.toBeInTheDocument()
})

// --- fix round 1: F5 - in-flight lock -----------------------------------

it('a double "aa" only approves once while the first approve is in flight', async () => {
  const approve = mockApprove({ delayMs: 150 })
  mockQueue([item({ outreach_id: 1, company_name: 'First Co' })])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('aa')
  expect(approve).toHaveBeenCalledTimes(1)
})

it('disables the action buttons while a mutation is in flight', async () => {
  // Two items, so the optimistic advance has somewhere real to go and the
  // panes (and ActionBar) stay mounted through the pending window - a
  // single-item queue's "nothing comes after this" case is exercised
  // separately by the empty-state test.
  mockApprove({ delayMs: 150 })
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
  ])
  render(<ReviewQueue />)
  const approveButton = await screen.findByRole('button', { name: /approve/i })
  await userEvent.click(approveButton)
  await waitFor(() => expect(screen.getByRole('button', { name: /approve/i })).toBeDisabled())
})

// --- fix round 1: T16-1 - visible buttons, same handlers as the keys ---

it('clicking Approve sends the same request as pressing "a"', async () => {
  const approve = mockApprove()
  mockQueue([item({ outreach_id: 1, company_name: 'First Co' })])
  render(<ReviewQueue />)
  const approveButton = await screen.findByRole('button', { name: /approve/i })
  await userEvent.click(approveButton)
  expect(approve).toHaveBeenCalledWith(1)
})

it('clicking Edit opens the same editor "e" does', async () => {
  mockQueue([item({ outreach_id: 1 })])
  render(<ReviewQueue />)
  const editButton = await screen.findByRole('button', { name: /^edit/i })
  await userEvent.click(editButton)
  expect(await screen.findByRole('textbox', { name: /body/i })).toBeInTheDocument()
})

// --- fix round 1: F6 - Escape cancels the editor ------------------------

it('Escape cancels the editor, even with focus still in the body textarea', async () => {
  mockQueue([item({ outreach_id: 1 })])
  render(<ReviewQueue />)
  await screen.findByText('Review queue')
  await userEvent.keyboard('e')
  const box = await screen.findByRole('textbox', { name: /body/i })
  box.focus()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('textbox', { name: /body/i })).not.toBeInTheDocument()
})

// --- beyond the brief's nine --------------------------------------------

it('"j"/"k" navigation stops at the ends instead of wrapping', async () => {
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)

  // "k" at the first item stays put.
  await userEvent.keyboard('k')
  expect(await screen.findByText(/1 of 2/)).toBeInTheDocument()
  expect(screen.getByText(/First Co/)).toBeInTheDocument()

  // "j" past the last item stays put too.
  await userEvent.keyboard('j')
  await screen.findByText(/2 of 2/)
  await userEvent.keyboard('j')
  expect(await screen.findByText(/2 of 2/)).toBeInTheDocument()
  expect(screen.getByText(/Second Co/)).toBeInTheDocument()
})

it('renders an ERROR critic verdict as "unavailable", never as a verdict', async () => {
  mockQueue([item({ critic_verdict: 'ERROR', critic_note: null })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/second opinion unavailable/i)).toBeInTheDocument()
  expect(screen.queryByText(/^PASS$/)).not.toBeInTheDocument()
  expect(screen.queryByText(/NEEDS_REVISION/)).not.toBeInTheDocument()
})

it('a 409 on approve (flagged draft) shows the reason and does not advance', async () => {
  mockApproveFailure(409, 'refusing to approve: this draft is flagged NEEDS MANUAL WRITE (bad email) - press [e] to edit it before approving')
  mockQueue([item({ outreach_id: 1, company_name: 'Flagged Co', last_error: 'bad email' })])
  render(<ReviewQueue />)
  await screen.findByText(/Flagged Co/)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/NEEDS MANUAL WRITE/)).toBeInTheDocument()
  expect(await screen.findByText(/1 of 1/)).toBeInTheDocument()
  expect(screen.getByText(/Flagged Co/)).toBeInTheDocument()
})

it('dismissing the critic note on item 1 does not hide it on item 2', async () => {
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co', critic_note: 'first note' }),
    item({ outreach_id: 2, company_name: 'Second Co', critic_note: 'second note' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/first note/)
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }))
  expect(screen.queryByText(/first note/)).not.toBeInTheDocument()

  await userEvent.keyboard('j')
  expect(await screen.findByText(/second note/)).toBeInTheDocument()
})

it('updates the counter after an approve', async () => {
  mockApprove()
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/1 of 2/)
  await userEvent.keyboard('a')
  // Fix round 2 (T16-5/F9): the counter is honest about what is still
  // actionable, not just what the (possibly stale) raw list still
  // contains - First Co is settled the moment it's approved, so with
  // one draft left to review this reads "1 of 1", not "2 of 2" (this
  // static mock never confirms the removal via a refetch, which is
  // exactly the "backend hasn't caught up yet" window F9 covers).
  expect(await screen.findByText(/1 of 1/)).toBeInTheDocument()
  expect(await screen.findByText(/Second Co/)).toBeInTheDocument()
})

it('shows the empty state once the queue is exhausted', async () => {
  mockApprove()
  mockQueueSequence([
    [item({ outreach_id: 1, company_name: 'Only Co' })],
    [],
  ])
  render(<ReviewQueue />)
  await screen.findByText(/Only Co/)
  await userEvent.keyboard('a')
  expect(await screen.findByText(/queue is empty/i)).toBeInTheDocument()
})

it('labels a sequence_step of exactly 1 as a follow-up (pins the > 0 boundary, not > 1)', async () => {
  mockQueue([item({ sequence_step: 1 })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/follow-up/i)).toBeInTheDocument()
  expect(screen.getByText(/follow-up number 1/i)).toBeInTheDocument()
})

it('labels a sequence_step > 0 draft as a follow-up', async () => {
  mockQueue([item({ sequence_step: 2 })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/follow-up/i)).toBeInTheDocument()
  expect(screen.getByText(/follow-up number 2/i)).toBeInTheDocument()
})

// --- 2026-09-16: the operator said "there is no resume attached" --------

it('names the resume attachment under the composed email', async () => {
  mockQueue([item({ attachment_name: 'Suryansh_Singh_resume.pdf' })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/Suryansh_Singh_resume\.pdf goes with it/)).toBeInTheDocument()
  expect(screen.queryByText(/No resume configured/)).not.toBeInTheDocument()
})

it('warns that send will refuse when no resume is configured (attachment_name null)', async () => {
  mockQueue([item({ attachment_name: null })])
  render(<ReviewQueue />)
  expect(await screen.findByText(/no resume file configured/i)).toBeInTheDocument()
  expect(screen.queryByText(/^Attachment:/)).not.toBeInTheDocument()
})

// --- fix round 2: F9 - settled ids stay hidden until the refetch lands ---

it(
  'after approving the only item, it is never re-shown (and cannot be re-approved) before the slow refetch confirms it',
  async () => {
    const approve = mockApprove()
    mockQueueSequence(
      [[item({ outreach_id: 1, company_name: 'Only Co' })], []],
      { delayMs: 400 },
    )
    render(<ReviewQueue />)
    await screen.findByText(/Only Co/)
    await userEvent.keyboard('a')
    expect(approve).toHaveBeenCalledTimes(1)

    // In the window before the slow refetch lands, "Only Co" must not be
    // shown as a live, actionable draft again.
    expect(await screen.findByText(/nothing left to review/i)).toBeInTheDocument()
    expect(screen.queryByText(/Only Co/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^approve/i })).not.toBeInTheDocument()

    // A second "a" in that window must not fire a second approve.
    await userEvent.keyboard('a')
    expect(approve).toHaveBeenCalledTimes(1)

    // Once the refetch confirms the row is really gone, the real empty
    // state takes over.
    expect(await screen.findByText(/queue is empty/i)).toBeInTheDocument()
  },
  8000,
)

it(
  'rapid approvals across a 3-item queue never re-show an already-acted-on draft while the refetch is slow',
  async () => {
    const approve = mockApprove()
    mockQueueSequence(
      [
        [
          item({ outreach_id: 1, company_name: 'First Co' }),
          item({ outreach_id: 2, company_name: 'Second Co' }),
          item({ outreach_id: 3, company_name: 'Third Co' }),
        ],
        [],
      ],
      { delayMs: 600 },
    )
    render(<ReviewQueue />)
    await screen.findByText(/First Co/)
    await userEvent.keyboard('a')
    await screen.findByText(/Second Co/)
    await userEvent.keyboard('a')
    await screen.findByText(/Third Co/)
    await userEvent.keyboard('a')

    expect(approve).toHaveBeenCalledTimes(3)
    expect(approve).toHaveBeenNthCalledWith(1, 1)
    expect(approve).toHaveBeenNthCalledWith(2, 2)
    expect(approve).toHaveBeenNthCalledWith(3, 3)

    // None of the three already-approved drafts is ever shown again -
    // the old bug would have re-surfaced Second Co ("2 of 3") here.
    expect(screen.queryByText(/First Co/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Second Co/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Third Co/)).not.toBeInTheDocument()
    expect(await screen.findByText(/nothing left to review/i)).toBeInTheDocument()
  },
  8000,
)

// --- fix round 2: F10 - navigation keys gated on busy, like the buttons -

it('navigation keys ("s"/"j"/"k") are inert while a mutation is in flight, matching the disabled Skip button', async () => {
  mockApprove({ delayMs: 200 })
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
    item({ outreach_id: 3, company_name: 'Third Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('a')
  await screen.findByText(/Second Co/)
  expect(screen.getByRole('button', { name: /skip/i })).toBeDisabled()

  await userEvent.keyboard('s')
  expect(screen.getByText(/Second Co/)).toBeInTheDocument()

  await userEvent.keyboard('j')
  expect(screen.getByText(/Second Co/)).toBeInTheDocument()

  await userEvent.keyboard('k')
  expect(screen.getByText(/Second Co/)).toBeInTheDocument()
})

// --- fix round 3: the settledIds filter made these branches vacuous in
// every shipped test (every test approved item 1, where
// `visibleItems[0]` happens to *be* the true neighbour) - each test
// below is built to make the filter-only fallback diverge from the real
// next/previous item. -----------------------------------------------

it('the optimistic advance lands on the true next item mid-list, not the start of the list', async () => {
  const approve = mockApprove()
  mockQueueSequence([
    [
      item({ outreach_id: 1, company_name: 'First Co' }),
      item({ outreach_id: 2, company_name: 'Second Co' }),
      item({ outreach_id: 3, company_name: 'Third Co' }),
    ],
    // Confirmed: item 2 (just approved) is gone.
    [
      item({ outreach_id: 1, company_name: 'First Co' }),
      item({ outreach_id: 3, company_name: 'Third Co' }),
    ],
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('j')
  await screen.findByText(/Second Co/)
  await userEvent.keyboard('a')
  expect(approve).toHaveBeenCalledWith(2)
  // The true next item is Third Co - a "fall back to the first visible
  // item" implementation (with the advance-target logic deleted) would
  // show First Co instead, since it happens to still be visible.
  expect(await screen.findByText(/Third Co/)).toBeInTheDocument()
  expect(screen.queryByText(/First Co/)).not.toBeInTheDocument()
})

it('the in-flight lock holds on a multi-item queue too - "aa" only approves once', async () => {
  const approve = mockApprove({ delayMs: 150 })
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
    item({ outreach_id: 3, company_name: 'Third Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('aa')
  expect(approve).toHaveBeenCalledTimes(1)
  expect(approve).toHaveBeenCalledWith(1)
})

it('goPrev is inert while busy, even from a middle item reached by falling back past the last one', async () => {
  const approve = mockApprove({ delayMs: 150 })
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
    item({ outreach_id: 3, company_name: 'Third Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('jj')
  await screen.findByText(/Third Co/)
  await userEvent.keyboard('a')
  expect(approve).toHaveBeenCalledWith(3)
  // Approving the last visible item falls back to the previous one -
  // Second Co - and that approve is still in flight (150ms).
  await screen.findByText(/Second Co/)
  await userEvent.keyboard('k')
  expect(screen.getByText(/Second Co/)).toBeInTheDocument()
  expect(screen.queryByText(/First Co/)).not.toBeInTheDocument()
})

it('prunes a settled id once confirmed gone, so the same outreach_id reappearing later is visible again', async () => {
  const redraft = mockRedraft()
  mockQueueSequence([
    [
      item({ outreach_id: 1, company_name: 'First Co' }),
      item({ outreach_id: 2, company_name: 'Second Co' }),
    ],
    // Confirmed: item 1 (redrafted -> READY_TO_DRAFT) is gone from the
    // review queue.
    [item({ outreach_id: 2, company_name: 'Second Co' })],
    // The draft job re-drafted it - the same outreach_id re-enters
    // PENDING_APPROVAL and shows up in /api/queue again.
    [item({ outreach_id: 1, company_name: 'First Co' })],
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('d')
  expect(redraft).toHaveBeenNthCalledWith(1, 1)
  expect(await screen.findByText(/Second Co/)).toBeInTheDocument()

  await userEvent.keyboard('d')
  expect(redraft).toHaveBeenNthCalledWith(2, 2)
  // A prune-free implementation would keep id 1 in `settledIds` forever,
  // hiding it even after the backend confirms it is a live draft again.
  expect(await screen.findByText(/First Co/)).toBeInTheDocument()
})

it('optional (S07): advancing past a settled item resolves the true visible neighbour, not a raw-list neighbour that lands on a hidden id', async () => {
  mockApprove()
  mockQueue([
    item({ outreach_id: 1, company_name: 'First Co' }),
    item({ outreach_id: 2, company_name: 'Second Co' }),
    item({ outreach_id: 3, company_name: 'Third Co' }),
    item({ outreach_id: 4, company_name: 'Fourth Co' }),
  ])
  render(<ReviewQueue />)
  await screen.findByText(/First Co/)
  await userEvent.keyboard('jj')
  await screen.findByText(/Third Co/)
  // Settles item 3, advances to item 4 (the true next visible item).
  await userEvent.keyboard('a')
  await screen.findByText(/Fourth Co/)
  // Settles item 4. Its true previous *visible* item, skipping the
  // already-hidden item 3, is item 2 - a raw-list neighbour lookup would
  // land on item 3 (hidden) and fall back to the first visible item
  // (item 1) instead.
  await userEvent.keyboard('a')
  expect(await screen.findByText(/Second Co/)).toBeInTheDocument()
  expect(screen.queryByText(/First Co/)).not.toBeInTheDocument()
})


it('saves an edit the checks dislike, and shows what they said', async () => {
  // The operator's own words are never refused: the checks are advice.
  mockQueue([item({})])
  const edit = mockEdit({ notes: ['implies seniority: my team'] })
  render(<ReviewQueue />)
  await userEvent.click(await screen.findByRole('button', { name: /^edit/i }))
  await userEvent.clear(screen.getByLabelText('Body'))
  await userEvent.type(screen.getByLabelText('Body'), 'I built it with my team.')
  await userEvent.click(screen.getByRole('button', { name: /save/i }))
  await waitFor(() => expect(edit).toHaveBeenCalled())
  // The editor closed - the save happened.
  await waitFor(() => expect(screen.queryByLabelText('Body')).not.toBeInTheDocument())
  expect(await screen.findByText('implies seniority: my team')).toBeInTheDocument()
})


// --- highlighting what a note is about (operator request 2026-09-22) -------

it('marks the words a note objects to, in the letter itself', async () => {
  mockQueue([item({ wire_text: 'Hi Madhav,\n\nOne thing; another thing.\n\nSuryansh' })])
  mockCheckDraft({
    notes: ['contains a semicolon'],
    highlights: [{ message: 'contains a semicolon', quote: ';' }],
  })
  render(<ReviewQueue />)
  await userEvent.click(await screen.findByRole('button', { name: /re-check/i }))
  const mark = await screen.findByLabelText('contains a semicolon')
  expect(mark.tagName).toBe('MARK')
  expect(mark).toHaveTextContent(';')
})

it('says which notes point at actual words', async () => {
  mockQueue([item({ wire_text: 'Hi Madhav,\n\nOne thing; another thing.\n\nSuryansh' })])
  mockCheckDraft({
    notes: ['contains a semicolon', 'body exceeds 380 words'],
    highlights: [
      { message: 'contains a semicolon', quote: ';' },
      { message: 'body exceeds 380 words', quote: null },
    ],
  })
  render(<ReviewQueue />)
  await userEvent.click(await screen.findByRole('button', { name: /re-check/i }))
  expect(await screen.findByRole('button', { name: /contains a semicolon.*highlighted/i }))
    .toBeEnabled()
  // A note with nothing to mark is not clickable - it would look like a
  // broken highlight.
  expect(screen.getByRole('button', { name: /body exceeds 380 words/ })).toBeDisabled()
})

it('a note with no quote leaves the letter unmarked', async () => {
  mockQueue([item({ wire_text: 'Hi Madhav,\n\nA clean letter.\n\nSuryansh' })])
  mockCheckDraft({
    notes: ['body exceeds 380 words'],
    highlights: [{ message: 'body exceeds 380 words', quote: null }],
  })
  const { container } = render(<ReviewQueue />)
  await userEvent.click(await screen.findByRole('button', { name: /re-check/i }))
  await screen.findByRole('button', { name: /body exceeds 380 words/ })
  expect(container.querySelectorAll('mark')).toHaveLength(0)
})


// --- the rewrite behind a highlight (operator request 2026-09-23) ----------
//
// "i want when i hover over it, it shows suggestion just like grammarly
// use sarvam for suggestion". `/check` is instant; `/suggest` is the LLM
// call, fired after it so the amber marks never wait on a model.

const MARKED = {
  wire_text: ['Hi Madhav,', '', 'One thing; another thing.', '', 'Suryansh'].join('\n'),
  body: 'One thing; another thing.',
}

function checkFindsASemicolon() {
  return mockCheckDraft({
    notes: ['contains a semicolon'],
    highlights: [{ message: 'contains a semicolon', quote: ';' }],
  })
}

it('asks for rewrites once the checks have found words to mark', async () => {
  mockQueue([item(MARKED)])
  checkFindsASemicolon()
  const suggest = mockSuggestFixes({ suggestions: [] })
  render(<ReviewQueue />)
  await screen.findByLabelText('contains a semicolon')
  await waitFor(() => expect(suggest).toHaveBeenCalled())
})

it('asks even when no check resolved to words, because the company paragraph always can', async () => {
  // 2026-09-23: this used to assert the screen did NOT ask. The company
  // paragraph is a rewrite candidate whether or not a check complained
  // about it, so the "is there anything to rewrite?" decision moved to
  // the backend, which has the letter and can answer it properly - and
  // which spends no model call when the answer is no
  // (tests/application/test_suggest_fixes.py).
  mockQueue([item({ wire_text: ['Hi Madhav,', '', 'A clean letter.', '', 'Suryansh'].join('\n') })])
  mockCheckDraft({
    notes: ['body exceeds 380 words'],
    highlights: [{ message: 'body exceeds 380 words', quote: null }],
  })
  const suggest = mockSuggestFixes({ suggestions: [] })
  render(<ReviewQueue />)
  expect(await screen.findByText('body exceeds 380 words')).toBeInTheDocument()
  await waitFor(() => expect(suggest).toHaveBeenCalled())
})

it('shows the rewrite when the mark is hovered', async () => {
  mockQueue([item(MARKED)])
  checkFindsASemicolon()
  mockSuggestFixes({
    suggestions: [{ index: 0, quote: ';', message: 'contains a semicolon',
                    replacement: '.', why: 'a full stop reads plainer' }],
  })
  render(<ReviewQueue />)
  await userEvent.hover(await screen.findByLabelText('contains a semicolon'))
  expect(await screen.findByText('a full stop reads plainer')).toBeInTheDocument()
})

it('accepting a rewrite saves the letter with the words replaced', async () => {
  mockQueue([item(MARKED)])
  checkFindsASemicolon()
  mockSuggestFixes({
    suggestions: [{ index: 0, quote: ';', message: 'contains a semicolon',
                    replacement: '.', why: 'plainer' }],
  })
  const edit = mockEdit({ notes: [] })
  render(<ReviewQueue />)
  await userEvent.hover(await screen.findByLabelText('contains a semicolon'))
  await userEvent.click(await screen.findByRole('button', { name: /use this/i }))
  // The quote is a substring of the BODY, never of the wire text - what
  // is saved is the body with those characters replaced, and nothing
  // else touched.
  await waitFor(() => expect(edit).toHaveBeenCalledWith(
    1, expect.objectContaining({ body: 'One thing. another thing.' }),
  ))
})

it('a failed rewrite call leaves the checks exactly where they were', async () => {
  mockQueue([item(MARKED)])
  checkFindsASemicolon()
  mockSuggestFailure()
  render(<ReviewQueue />)
  // The highlight and the note are both still there; only the rewrite is
  // missing, and the hover card says so.
  const mark = await screen.findByLabelText('contains a semicolon')
  await userEvent.hover(mark)
  expect(await screen.findByText(/no rewrite for this one/i)).toBeInTheDocument()
})


// --- the generic company line (operator request 2026-09-23) ---------------
//
// "give option for this to make a generic if i am not able to find good
// for this specifically line". The realistic alternative to a generic
// line is not a better specific one - it is a fabricated one.

it('offers a way to swap in a generic company line', async () => {
  mockQueue([item({})])
  const generic = mockGenericLine({ notes: [] })
  render(<ReviewQueue />)
  await userEvent.click(await screen.findByRole('button', { name: /use a generic line/i }))
  await waitFor(() => expect(generic).toHaveBeenCalledWith(1))
})

it('shows what the checks made of the generic line', async () => {
  // It is an edit, so its result is the checks' verdict on the new text
  // - shown exactly where a save's would be.
  mockQueue([item({})])
  mockGenericLine({ notes: ['hook shares no distinctive term with the cited source'] })
  render(<ReviewQueue />)
  await userEvent.click(await screen.findByRole('button', { name: /use a generic line/i }))
  expect(
    await screen.findByText('hook shares no distinctive term with the cited source'),
  ).toBeInTheDocument()
})

it('says so when a letter has no company paragraph to swap', async () => {
  mockQueue([item({})])
  mockGenericLineFailure('this letter does not have the assembled shape - edit it by hand')
  render(<ReviewQueue />)
  await userEvent.click(await screen.findByRole('button', { name: /use a generic line/i }))
  expect(await screen.findByText(/edit it by hand/i)).toBeInTheDocument()
})

it('shows the generic line on hover, before it is swapped in', async () => {
  // "also give the hover effect for generic line also" - the same read-
  // before-you-commit the amber highlights give.
  mockQueue([item({})])
  const generic = mockGenericLine({ notes: [] })
  render(<ReviewQueue />)
  await userEvent.hover(await screen.findByRole('button', { name: /use a generic line/i }))
  expect(await screen.findByText(/replaces the paragraph about them with/i)).toBeInTheDocument()
  expect(screen.getByText(/yours is one I kept reading about/)).toBeInTheDocument()
  // Hovering is a read: nothing is written until the button is pressed.
  expect(generic).not.toHaveBeenCalled()
})

it('offers no generic line for a letter that has no company paragraph', async () => {
  // The backend refuses the swap for a letter without the assembled
  // shape, and says so by sending no line. The button must not promise
  // what the backend will decline.
  mockQueue([item({ generic_line: null })])
  render(<ReviewQueue />)
  await screen.findByText('Review queue')
  expect(screen.queryByRole('button', { name: /use a generic line/i })).not.toBeInTheDocument()
})

it('marks the company paragraph in the letter, so the swap shows its work', async () => {
  // "no highlight that section to" - the text that would disappear is on
  // screen next to the text that would take its place.
  mockQueue([item({
    wire_text: ['Hi Akash,', '', 'You built a voice layer.', '', 'Suryansh'].join('\n'),
    body: 'You built a voice layer.',
    insight_text: 'You built a voice layer.',
  })])
  render(<ReviewQueue />)
  const mark = await screen.findByLabelText(/paragraph about them/i)
  expect(mark).toHaveAttribute('data-kind', 'insight')
})

it('swaps the generic line in from the paragraph itself', async () => {
  mockQueue([item({
    wire_text: ['Hi Akash,', '', 'You built a voice layer.', '', 'Suryansh'].join('\n'),
    body: 'You built a voice layer.',
    insight_text: 'You built a voice layer.',
  })])
  const generic = mockGenericLine({ notes: [] })
  render(<ReviewQueue />)
  await userEvent.hover(await screen.findByLabelText(/paragraph about them/i))
  const buttons = await screen.findAllByRole('button', { name: /use a generic line/i })
  await userEvent.click(buttons[buttons.length - 1])
  await waitFor(() => expect(generic).toHaveBeenCalledWith(1))
})

it('leaves the letter unmarked when there is no company paragraph to point at', async () => {
  mockQueue([item({ insight_text: null })])
  render(<ReviewQueue />)
  await screen.findByText('Review queue')
  expect(screen.queryByLabelText(/paragraph about them/i)).not.toBeInTheDocument()
})

it('keeps the generic option on a check mark that covers the company paragraph', async () => {
  // The check wins the overlap, so without this the generic line would
  // vanish from exactly the letters most likely to need it - the ones a
  // hook check has flagged.
  const paragraph = 'You built a voice layer.'
  mockQueue([item({
    wire_text: ['Hi Akash,', '', paragraph, '', 'Suryansh'].join('\n'),
    body: paragraph,
    insight_text: paragraph,
  })])
  mockCheckDraft({
    notes: ['hook overlaps more than 60% with one_liner'],
    highlights: [{ message: 'hook overlaps more than 60% with one_liner', quote: paragraph }],
  })
  const generic = mockGenericLine({ notes: [] })
  render(<ReviewQueue />)
  // One mark, and it is the check's (amber) - but it carries both tabs.
  const mark = await screen.findByLabelText(/hook overlaps more than 60%/i)
  expect(mark).toHaveAttribute('data-kind', 'check')
  await userEvent.hover(mark)
  await userEvent.click(await screen.findByRole('tab', { name: /generic/i }))
  // Two on screen: the one under the letter and the one in the card.
  // The card's is the one this test is about, and it is rendered last.
  const buttons = await screen.findAllByRole('button', { name: /use a generic line/i })
  await userEvent.click(buttons[buttons.length - 1])
  await waitFor(() => expect(generic).toHaveBeenCalledWith(1))
})
