// The letter. Ruling R2: this renders `item.wire_text` (greeting,
// signature and opt-out line already applied), never the bare stored
// `item.body` - the operator reviews what actually goes out.
//
// Everything that is *about* the letter rather than part of it - the
// critic's verdict, the validator's complaint, the attachment - moved to
// the docket beside it (SourcePane). The sheet holds the addressing
// block, the subject and the letter itself, so what you read here is
// what the founder reads.
export default function DraftPane({ item }) {
  if (!item) return null

  return (
    <article className="draft-pane" aria-label="Draft email">
      <h2>Draft</h2>

      {item.sequence_step > 0 && (
        <p className="sequence-step-badge">Follow-up number {item.sequence_step}</p>
      )}

      <dl className="letter-head">
        <dt>To</dt>
        <dd>{item.to ?? 'no address yet'}</dd>
        <dt>Subject</dt>
        <dd className="letter-subject">{item.subject ?? '(no subject)'}</dd>
      </dl>

      <pre className="wire-text">{item.wire_text}</pre>

      {/* Live finding (2026-09-16): the operator reviewed a draft and said
          "there is no resume attached". It is - `send` attaches
          RESUME_PATH - but nothing said so. `attachment_name` is null
          exactly when `send` would refuse, so say that instead. */}
      {item.attachment_name ? (
        <p className="letter-foot">
          {item.attachment_name} goes with it · {item.word_count} words
        </p>
      ) : (
        <p className="letter-foot attachment-missing" role="alert">
          No resume file configured, so this cannot be sent yet · {item.word_count} words
        </p>
      )}
    </article>
  )
}
