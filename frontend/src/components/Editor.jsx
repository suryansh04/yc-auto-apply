// Task 16: the manual-write / edit form. Subject + body are local,
// controlled state seeded once from `initialSubject`/`initialBody` -
// deliberately NOT reset when `errors` changes, so a 422 from the
// validator (POST /outreach/:id/edit) never wipes out what the operator
// typed (the brief's "keeps the typed text when an edit is rejected"
// requirement). The parent decides when to actually close this (only on
// a successful save) and re-mounts it only when a different draft is
// opened for editing.
import { useState } from 'react'

export default function Editor({ initialSubject, initialBody, errors, onSave, onCancel }) {
  const [subject, setSubject] = useState(initialSubject ?? '')
  const [body, setBody] = useState(initialBody ?? '')

  return (
    <section className="editor" aria-label="Edit draft">
      <h2>Edit draft</h2>

      {errors && errors.length > 0 && (
        <ul className="editor-errors" role="alert">
          {errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}

      <label className="editor-field">
        Subject
        <input
          type="text"
          aria-label="Subject"
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
        />
      </label>

      <label className="editor-field">
        Body
        <textarea
          aria-label="Body"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={12}
        />
      </label>

      <div className="editor-actions">
        <button type="button" onClick={() => onSave(subject, body)}>
          Save
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  )
}
