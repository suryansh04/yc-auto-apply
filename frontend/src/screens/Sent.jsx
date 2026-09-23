// What actually went out (operator request 2026-09-23: "Make a Page for
// sent email too").
//
// The Applications screen answers "how is this going?" across all 284
// founders; its `sent` filter is one row per letter and no letter text.
// This screen answers a different question - "what did I actually send
// that person, and did they write back?" - so it is built the other way
// round: a short list on the left, and the letter as it left the mailbox
// on the right, read without leaving the page.
//
// Two requests, both already in the cache the rest of the app uses:
// `/api/applications` for the list (284 rows / 58 KB, measured - see
// lib/paging.js, so the paging stays client-side) and
// `/api/outreach/{id}/thread` for the one letter being read. Replies come
// with the thread, so a founder's answer sits under the letter that
// prompted it.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { useApplications, useThread } from '../api/hooks'
import { useIncrementalRows } from '@/lib/paging'
import { outreachStateLabel } from '../labels'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Item, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'

// The four states that all mean "it left the mailbox"
// (app/api/routers/applications.py's _SENT_STATES). They are one outcome
// and four different things to know, so the state is always shown.
const REPLY_VARIANT = {
  replied: 'default',
  bounced: 'destructive',
  closed: 'secondary',
  sent: 'outline',
}

function when(iso, { withTime = false } = {}) {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return withTime ? date.toLocaleString() : date.toLocaleDateString()
}

/** The letter as it left, plus anything that came back. */
function Letter({ outreachId }) {
  const threadQuery = useThread(outreachId)

  if (threadQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-64" />
        </CardHeader>
        <CardContent className="grid gap-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </CardContent>
      </Card>
    )
  }

  if (threadQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load this letter</AlertTitle>
        <AlertDescription>{threadQuery.error.message}</AlertDescription>
      </Alert>
    )
  }

  const thread = threadQuery.data
  if (!thread) return null
  // Only what was actually sent: an outbound row with no `sent_at` is
  // still a draft, and calling a draft "sent" on the Sent page would be
  // the one lie this screen must not tell.
  const messages = thread.messages.filter(
    (m) => m.direction === 'inbound' || m.sent_at,
  )

  return (
    <div className="grid gap-4">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">
          {thread.founder_name ?? 'Founder'} at {thread.company_name ?? 'Unknown company'}
        </h2>
        <p className="text-muted-foreground text-sm">
          {thread.to ?? 'no address'} &mdash; {outreachStateLabel(thread.state)}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {thread.gmail_url && (
            <Button asChild variant="outline" size="sm">
              <a href={thread.gmail_url} target="_blank" rel="noreferrer">
                Open in Gmail <ExternalLink />
              </a>
            </Button>
          )}
          {/* The Thread screen is where a reply gets re-labelled; this
              page reads, it does not sort. */}
          <Button asChild variant="ghost" size="sm">
            <Link to={`/thread/${outreachId}`}>Open the full conversation</Link>
          </Button>
        </div>
      </div>

      {messages.map((message) => (
        <Card key={message.id} className={message.direction === 'inbound' ? 'bg-muted/40' : undefined}>
          <CardHeader>
            <CardDescription className="flex flex-wrap items-center gap-2">
              <Badge variant={message.direction === 'inbound' ? 'secondary' : 'outline'}>
                {message.direction === 'inbound' ? 'Their reply' : 'You sent'}
              </Badge>
              {when(message.sent_at ?? message.received_at, { withTime: true })}
              {message.sequence_step > 0 && (
                <Badge variant="secondary">Follow-up {message.sequence_step}</Badge>
              )}
            </CardDescription>
            {message.subject && <CardTitle className="text-base">{message.subject}</CardTitle>}
          </CardHeader>
          <Separator />
          <CardContent>
            <pre className="font-sans text-[15px] leading-relaxed whitespace-pre-wrap">
              {message.body}
            </pre>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export default function Sent() {
  const query = useApplications()
  const [openId, setOpenId] = useState(null)

  const rows = (query.data ?? [])
    .filter((a) => a.outcome === 'sent')
    // Newest first: the last thing sent is the thing being thought about.
    .sort((a, b) => String(b.sent_at ?? '').localeCompare(String(a.sent_at ?? '')))
  const page = useIncrementalRows(rows)
  // The first letter is open on arrival, so the page reads as a letter
  // rather than as a list with an empty half. Never forced afterwards:
  // once a row has been picked, that is the one being read.
  const currentId = openId ?? rows[0]?.outreach_id ?? null

  if (query.isLoading) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load sent mail</AlertTitle>
        <AlertDescription>{query.error.message}</AlertDescription>
      </Alert>
    )
  }

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sent</h1>
        <p className="text-muted-foreground text-sm">
          Every letter that left the mailbox, and anything that came back.
        </p>
      </div>

      {rows.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Nothing has been sent yet</EmptyTitle>
            <EmptyDescription>
              Approve a letter in Review, then send it from{' '}
              <Link className="underline underline-offset-4" to="/approved">
                Approved
              </Link>
              . It appears here the moment it goes out.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
          <div className="grid gap-2">
            {page.visible.map((row) => (
              <button
                key={row.outreach_id}
                type="button"
                className="text-left"
                aria-current={row.outreach_id === currentId ? 'true' : undefined}
                onClick={() => setOpenId(row.outreach_id)}
              >
                <Item
                  variant={row.outreach_id === currentId ? 'outline' : 'muted'}
                  className={row.outreach_id === currentId ? 'border-primary/60' : undefined}
                >
                  <ItemContent>
                    <ItemTitle>{row.company_name ?? 'Unknown company'}</ItemTitle>
                    <ItemDescription>
                      {row.founder_name} &mdash; {when(row.sent_at) ?? 'date unknown'}
                    </ItemDescription>
                    <Badge variant={REPLY_VARIANT[row.state] ?? 'outline'} className="mt-1 w-fit">
                      {outreachStateLabel(row.state)}
                    </Badge>
                  </ItemContent>
                </Item>
              </button>
            ))}

            <div className="flex items-center justify-center gap-3 py-1">
              <span className="text-muted-foreground text-sm">
                Showing {page.shown} of {page.total}
              </span>
              {page.hasMore && (
                <Button variant="outline" size="sm" onClick={page.loadMore}>
                  Load more
                </Button>
              )}
              <span ref={page.sentinelRef} aria-hidden="true" />
            </div>
          </div>

          {currentId != null && <Letter outreachId={currentId} />}
        </div>
      )}
    </>
  )
}
