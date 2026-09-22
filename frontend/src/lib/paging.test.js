// Incremental rendering for the long lists (operator request 2026-09-22).
//
// Measured before building: /api/applications is 284 rows in 58 KB. The
// network is not the problem at that size - putting two thousand table
// cells in the DOM at once is. So the whole list is fetched as before and
// revealed a page at a time.
import { expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { DEFAULT_PAGE_SIZE, useIncrementalRows } from './paging'

const rows = (n) => Array.from({ length: n }, (_, i) => ({ id: i }))

it('shows one page to start with and reports the whole total', () => {
  const { result } = renderHook(() => useIncrementalRows(rows(284)))
  expect(result.current.visible).toHaveLength(DEFAULT_PAGE_SIZE)
  expect(result.current.shown).toBe(DEFAULT_PAGE_SIZE)
  // The total is the real one, not what is on screen - "Showing 50 of
  // 284" is the point.
  expect(result.current.total).toBe(284)
  expect(result.current.hasMore).toBe(true)
})

it('reveals another page each time', () => {
  const { result } = renderHook(() => useIncrementalRows(rows(120), { pageSize: 50 }))
  act(() => result.current.loadMore())
  expect(result.current.visible).toHaveLength(100)
  act(() => result.current.loadMore())
  expect(result.current.visible).toHaveLength(120)
  expect(result.current.hasMore).toBe(false)
})

it('never claims more rows than there are', () => {
  const { result } = renderHook(() => useIncrementalRows(rows(3), { pageSize: 50 }))
  expect(result.current.visible).toHaveLength(3)
  expect(result.current.shown).toBe(3)
  expect(result.current.hasMore).toBe(false)
})

it('handles an empty list', () => {
  const { result } = renderHook(() => useIncrementalRows([]))
  expect(result.current.visible).toEqual([])
  expect(result.current.total).toBe(0)
  expect(result.current.hasMore).toBe(false)
})

it('goes back to the first page when the filter changes', () => {
  // A new filter must not leave you 200 rows deep in a list you have not
  // seen.
  const { result, rerender } = renderHook(
    ({ key }) => useIncrementalRows(rows(284), { pageSize: 50, resetKey: key }),
    { initialProps: { key: 'all' } },
  )
  act(() => result.current.loadMore())
  expect(result.current.shown).toBe(100)
  rerender({ key: 'sent' })
  expect(result.current.shown).toBe(50)
})

it('stays where it is when the filter has not changed', () => {
  const { result, rerender } = renderHook(
    ({ key }) => useIncrementalRows(rows(284), { pageSize: 50, resetKey: key }),
    { initialProps: { key: 'all' } },
  )
  act(() => result.current.loadMore())
  rerender({ key: 'all' })
  expect(result.current.shown).toBe(100)
})

it('keeps the rows in the order it was given', () => {
  const { result } = renderHook(() => useIncrementalRows(rows(10), { pageSize: 4 }))
  expect(result.current.visible.map((r) => r.id)).toEqual([0, 1, 2, 3])
})
