// Task 16: the right-hand pane - who this draft is going to, and the
// specific research row it cites, side by side with the draft (see
// DraftPane) so the operator can check a claim without leaving the
// screen. Deliberately dumb: all it does is read fields off the
// `QueueItemOut` it is given.
export default function SourcePane({ item }) {
  if (!item) return null

  return (
    <section className="source-pane" aria-label="Company and source">
      <h2>{item.company_name ?? 'Unknown company'}</h2>
      {item.company_one_liner && <p className="company-one-liner">{item.company_one_liner}</p>}

      <dl className="founder-details">
        <dt>Founder</dt>
        <dd>
          {item.founder_name}
          {item.founder_title ? ` — ${item.founder_title}` : ''}
        </dd>
        <dt>To</dt>
        <dd>{item.to ?? 'no resolved email'}</dd>
      </dl>

      <div className="cited-source">
        <h3>Cited source</h3>
        {item.source_text ? (
          <>
            <p className="source-kind">{item.source_kind}</p>
            <p className="source-text">{item.source_text}</p>
            {item.source_publication && (
              <p className="source-publication">{item.source_publication}</p>
            )}
          </>
        ) : (
          <p className="source-text source-text-none">no citation (manual write)</p>
        )}
      </div>
    </section>
  )
}
