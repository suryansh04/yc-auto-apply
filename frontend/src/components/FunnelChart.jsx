// Where everyone stands, as bar lengths instead of a column of numbers.
//
// The job here is magnitude across an ordered pipeline, so: one series,
// one hue (length carries the value, not colour), bars in pipeline order
// rather than sorted by size - the order is the story. Every bar is
// labelled with its exact count, because the table this replaced was
// read for exact counts and losing them would be a downgrade.
//
// A stage with nobody in it keeps its row: "Sent 0" is a fact about the
// pipeline, and dropping empty rows would silently reshuffle the chart
// every time a stage drained.
import { Link } from 'react-router-dom'
import { outreachStateLabel } from '../labels'

export default function FunnelChart({ counts, states, linkFor, max }) {
  const rows = states.map((state) => ({
    state,
    label: outreachStateLabel(state),
    value: counts[state] ?? 0,
  }))
  // Every chart on a screen shares one scale, supplied by the caller.
  // Scaling each group to its own largest bar made "Discarded 1" as wide
  // as "Not started 162" - two charts read as one picture, so a bar's
  // length has to mean the same thing in both.
  //
  // The scale is the largest bar, never the total: with 162 in one stage
  // and 8 in another, a total-based scale would render most of the
  // pipeline as invisible slivers.
  const scale = Math.max(max ?? 0, ...rows.map((row) => row.value), 1)

  return (
    <ul className="funnel-chart">
      {rows.map(({ state, label, value }) => {
        const to = linkFor?.(state)
        const name = to ? <Link to={to}>{label}</Link> : label
        return (
          <li key={state} className="funnel-row">
            <span className="funnel-label">{name}</span>
            <span className="funnel-track" title={`${label}: ${value}`}>
              <span
                className="funnel-bar"
                style={{ width: `${(value / scale) * 100}%` }}
                aria-hidden="true"
              />
            </span>
            <span className="funnel-value">{value}</span>
          </li>
        )
      })}
    </ul>
  )
}
