// One vocabulary for the whole app.
//
// `OutreachState` (backend/app/domain/states.py) is a database enum:
// `needs_manual_email`, `pending_approval`. Those names are how the
// system is built, not how the person using it thinks, and when the
// Pipeline said "Pending approval" while Founders said
// "pending_approval" the same row appeared to be in two different
// states. An action the user can take keeps the same name everywhere it
// appears, so the words are signposting rather than noise.
export const OUTREACH_STATE_LABELS = {
  new: 'Not started',
  needs_email: 'Looking for an address',
  needs_manual_email: 'Needs an address',
  ready_to_draft: 'Ready to write',
  pending_approval: 'Waiting for review',
  approved: 'Approved, not sent',
  sent: 'Sent',
  replied: 'They replied',
  closed: 'Closed',
  discarded: 'Discarded',
  bounced: 'Address bounced',
  unresolved: 'No address found',
  failed: 'Something failed',
}

export function outreachStateLabel(state) {
  return OUTREACH_STATE_LABELS[state] ?? state
}
