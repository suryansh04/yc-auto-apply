// Task 17: the Thread screen - the operator's whole-conversation read
// view for one outreach (backend/app/api/routers/thread.py's
// `GET /api/outreach/{id}/thread`). Every outbound message and every
// reply, already in time order from the backend, a reply's label with
// its confidence/rationale, a one-click correction control, and a link
// out to the operator's own Gmail thread.
import { Link, useParams } from 'react-router-dom'
import { useState } from 'react'
import { useSetLabel, useThread } from '../api/hooks'
import Banner from '../components/Banner'

// app/domain/replies.py's LABELS/MACHINE_LABELS, mirrored by hand (no
// generated types) - the six labels an operator can correct a reply to,
// and which two of them are the automatic classifier's own signals
// (an autoresponder, an out-of-office bounce) rather than a founder's own
// words. An operator glancing at the timeline must never mistake one for
// the other, so these get distinct styling (`.label-machine` vs
// `.label-human`), not just distinct text.
const LABELS = ['INTERESTED', 'REJECTED', 'REFERRAL', 'AUTO_REPLY', 'OUT_OF_OFFICE', 'UNCLEAR']
const MACHINE_LABELS = new Set(['AUTO_REPLY', 'OUT_OF_OFFICE'])

// The value sent to the backend is the label; the text on screen is what
// the reply actually was. Shouting INTERESTED at the operator tells them
// nothing the sentence case doesn't.
const LABEL_TEXT = {
  INTERESTED: 'Interested',
  REJECTED: 'Not interested',
  REFERRAL: 'Referred me on',
  AUTO_REPLY: 'Automatic reply',
  OUT_OF_OFFICE: 'Out of office',
  UNCLEAR: 'Unclear',
}

function LabelBadge({ label, confidence, rationale, corrected }) {
  if (!label) return <span className="label label-none">Not sorted yet</span>
  const machine = MACHINE_LABELS.has(label)
  return (
    <div className={machine ? 'label label-machine' : 'label label-human'}>
      <span className="label-name">{LABEL_TEXT[label] ?? label}</span>
      {machine && <span className="label-machine-tag">automated</span>}
      {confidence != null && (
        <span className="label-confidence">confidence {Math.round(confidence * 100)}%</span>
      )}
      {rationale && <span className="label-rationale">{rationale}</span>}
      {corrected && <span className="label-corrected">you changed this</span>}
    </div>
  )
}

// The one-click correction control: one <select> per reply (still one
// interaction, one control) rather than six always-visible buttons -
// twelve buttons on a two-reply thread was the problem this replaced.
// Picking an option fires the correction immediately; the select itself
// stays on the placeholder afterwards (it's a one-shot action menu, not
// a persistent field showing "the current label" - that's LabelBadge's
// job, right above it).
function LabelCorrection({ message, threadId, setLabel }) {
  const [error, setError] = useState(null)

  function correct(event) {
    const label = event.target.value
    event.target.value = ''
    if (!label) return
    setError(null)
    setLabel.mutate(
      { messageId: message.id, label, threadId },
      { onError: (err) => setError(err.message) },
    )
  }

  return (
    <div className="label-correction">
      <label>
        Correct label
        <select
          aria-label={`Correct label for message ${message.id}`}
          defaultValue=""
          disabled={setLabel.isPending}
          onChange={correct}
        >
          <option value="" disabled>
            Change label&hellip;
          </option>
          {LABELS.map((label) => (
            <option key={label} value={label}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
    </div>
  )
}

function MessageRow({ message, threadId, setLabel }) {
  const timestamp = message.sent_at ?? message.received_at
  return (
    <li className={`message message-${message.direction}`}>
      <div className="message-meta">
        <span className="message-direction">
          {/* V3 (T18 fix wave 2): an outbound row with no `sent_at` is
              still pending_approval/approved - nothing was actually sent
              yet, so labelling it "Sent" is a lie the operator would
              act on (e.g. assuming a follow-up already went out). */}
          {message.direction === 'outbound'
            ? message.sent_at
              ? 'Sent'
              : 'Draft'
            : 'Reply'}
        </span>
        {timestamp && <time dateTime={timestamp}>{new Date(timestamp).toLocaleString()}</time>}
      </div>
      {message.subject && <p className="message-subject">{message.subject}</p>}
      <p className="message-body">{message.body}</p>
      {message.direction === 'inbound' && (
        <>
          <LabelBadge
            label={message.label}
            confidence={message.confidence}
            rationale={message.rationale}
            corrected={message.corrected}
          />
          <LabelCorrection message={message} threadId={threadId} setLabel={setLabel} />
        </>
      )}
    </li>
  )
}

export default function Thread() {
  const { id } = useParams()
  const outreachId = Number(id)
  const threadQuery = useThread(outreachId)
  const setLabel = useSetLabel()

  if (threadQuery.isLoading) return <p className="muted">Loading…</p>

  if (threadQuery.isError) {
    if (threadQuery.error?.status === 404) {
      return (
        <div className="thread">
          <p className="not-found">That conversation does not exist.</p>
          <Link to="/contacts">Back to founders</Link>
        </div>
      )
    }
    return <Banner kind="error">Could not load thread: {threadQuery.error.message}</Banner>
  }

  const thread = threadQuery.data
  if (!thread) return null

  return (
    <div className="thread">
      <h1>
        {thread.founder_name ?? 'Founder'} at {thread.company_name ?? 'Unknown company'}
      </h1>
      <p className="thread-meta">
        {thread.to} &mdash; {thread.state}
      </p>
      {thread.gmail_url && (
        <a href={thread.gmail_url} target="_blank" rel="noreferrer">
          Open in Gmail
        </a>
      )}
      <ol className="message-list">
        {thread.messages.map((message) => (
          <MessageRow key={message.id} message={message} threadId={outreachId} setLabel={setLabel} />
        ))}
      </ol>
    </div>
  )
}
