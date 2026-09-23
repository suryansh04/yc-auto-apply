// Render a long list a page at a time (operator request, 2026-09-22).
//
// Measured first, because the obvious fix would have been the wrong one:
// /api/applications is 284 rows in 58 KB and /api/contacts 284 in 63 KB.
// The network is not the problem at that size - paging on the SERVER
// would add a round trip per page for no gain, and the Applications tab
// counts need the whole set anyway. What costs is putting two thousand
// table cells into the DOM at once.
//
// So the whole list is fetched once, as before, and rendered
// incrementally. `loadMore` is a real button, and an IntersectionObserver
// presses it when the sentinel scrolls into view - the scrolling is the
// convenience, the button is the contract. Without an observer (jsdom,
// an old browser) the button still works on its own.
import { useEffect, useRef, useState } from 'react'

export const DEFAULT_PAGE_SIZE = 50

/**
 * `rows`: the full list, already filtered and sorted.
 * `pageSize`: how many more to reveal each time.
 * `resetKey`: change it to go back to the first page - a new filter
 *   should not leave you 200 rows deep in a list you have not seen.
 *
 * Returns `{ visible, total, shown, hasMore, loadMore, sentinelRef }`.
 */
export function useIncrementalRows(rows, { pageSize = DEFAULT_PAGE_SIZE, resetKey = '' } = {}) {
  const [shown, setShown] = useState(pageSize)
  const [lastKey, setLastKey] = useState(resetKey)

  // Adjusted during render rather than in an effect (the React docs'
  // "adjusting state when a prop changes"): rendering 200 rows and then
  // throwing them away on the next tick is exactly the work this hook
  // exists to avoid.
  if (resetKey !== lastKey) {
    setLastKey(resetKey)
    setShown(pageSize)
  }

  const total = rows.length
  const visibleCount = Math.min(shown, total)
  const hasMore = visibleCount < total

  function loadMore() {
    setShown((current) => current + pageSize)
  }

  const sentinelRef = useRef(null)
  useEffect(() => {
    const node = sentinelRef.current
    if (!node || !hasMore || typeof IntersectionObserver !== 'function') return undefined
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore()
    })
    observer.observe(node)
    return () => observer.disconnect()
    // `visibleCount` so a fresh observer watches the sentinel after each
    // page: the old one was disconnected and the node has moved down.
  }, [hasMore, visibleCount])

  return {
    visible: hasMore || shown > total ? rows.slice(0, visibleCount) : rows,
    total,
    shown: visibleCount,
    hasMore,
    loadMore,
    sentinelRef,
  }
}
