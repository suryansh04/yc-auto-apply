// Column sorting for the two dense tables (Founders, Missing addresses).
//
// A hook and two pure functions rather than a table component: the tables
// themselves are shadcn's `Table` primitives, and this supplies only the
// ordering they display in. Ties are broken by the row's key so the
// displayed order is deterministic regardless of the array's original
// order or whatever stability guarantee the engine's sort happens to
// give - the screen tests rely on this tiebreak directly.
import { useState } from 'react'

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
 * `columns`: array of { key, label, accessor?(row), sortable? }.
 * `rows`: the data in any order.
 * `getRowKey(row)`: stable unique key, also the sort's tiebreak.
 * `defaultSortKey`: sorted by (ascending) before any header is clicked.
 *
 * Returns `{ rows, sortKey, sortDir, toggleSort, ariaSort }`.
 */
export function useSortedRows({ columns, rows, getRowKey, defaultSortKey = null }) {
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

  function ariaSort(column) {
    if (sortKey !== column.key) return 'none'
    return sortDir === 'asc' ? 'ascending' : 'descending'
  }

  const activeColumn = columns.find((c) => c.key === sortKey)
  const sortedRows = activeColumn
    ? [...rows].sort((a, b) => {
        const accessor = activeColumn.accessor ?? ((row) => row[activeColumn.key])
        const result = compareRows(a, b, accessor, getRowKey)
        return sortDir === 'asc' ? result : -result
      })
    : rows

  return { rows: sortedRows, sortKey, sortDir, toggleSort, ariaSort }
}
