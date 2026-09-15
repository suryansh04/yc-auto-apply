// Task 17: a plain, dense HTML table shared by Contacts and Manual email -
// no card grid. Sortable by clicking a sortable column's header; ties are
// broken by `getRowKey` (stringified) so the displayed order is
// deterministic regardless of the underlying array's original order or
// whatever stability guarantee the JS engine's own sort happens to give -
// screen tests rely on this tiebreak directly.
import { useState } from 'react'

function defaultAccessor(column) {
  return (row) => row[column.key]
}

function compareRows(a, b, accessor, getRowKey) {
  const av = accessor(a)
  const bv = accessor(b)
  if (av < bv) return -1
  if (av > bv) return 1
  const ak = String(getRowKey(a))
  const bk = String(getRowKey(b))
  if (ak < bk) return -1
  if (ak > bk) return 1
  return 0
}

/**
 * `columns`: array of
 *   { key, label, accessor?(row), render?(row), sortable? }
 * - `accessor` supplies the value sorted on (defaults to `row[key]`).
 * - `render` supplies the cell's content (defaults to the accessor's
 *   value) - used for links, lists, forms, anything not plain text.
 * - `sortable` defaults to true; set `false` for a column with no
 *   natural sort order (a link, a form, a list of candidates).
 *
 * `rows`: the data, in any order - DataTable owns display order once a
 * sort is active.
 *
 * `getRowKey(row)`: a stable unique key, also used as the sort's
 * secondary (tiebreak) key.
 *
 * `defaultSortKey`: the column key sorted by (ascending) before any
 * header has been clicked. `null`/omitted leaves `rows` in its given
 * order until a header is clicked.
 */
export default function DataTable({ columns, rows, getRowKey, defaultSortKey = null }) {
  const [sortKey, setSortKey] = useState(defaultSortKey)
  const [sortDir, setSortDir] = useState('asc')

  function toggleSort(column) {
    if (column.sortable === false) return
    if (sortKey === column.key) {
      setSortDir((dir) => (dir === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(column.key)
      setSortDir('asc')
    }
  }

  const activeColumn = columns.find((c) => c.key === sortKey)
  const sortedRows = activeColumn
    ? [...rows].sort((a, b) => {
        const accessor = activeColumn.accessor ?? defaultAccessor(activeColumn)
        const result = compareRows(a, b, accessor, getRowKey)
        return sortDir === 'asc' ? result : -result
      })
    : rows

  return (
    <table className="data-table">
      <thead>
        <tr>
          {columns.map((column) => (
            <th
              key={column.key}
              scope="col"
              aria-sort={
                sortKey === column.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'
              }
            >
              {column.sortable === false ? (
                column.label
              ) : (
                <button type="button" onClick={() => toggleSort(column)}>
                  {column.label}
                  {sortKey === column.key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
                </button>
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sortedRows.map((row) => (
          <tr key={getRowKey(row)}>
            {columns.map((column) => (
              <td key={column.key}>
                {column.render
                  ? column.render(row)
                  : (column.accessor ?? defaultAccessor(column))(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
