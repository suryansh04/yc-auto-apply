// Every application, by what became of it (operator request 2026-09-22).
//
// The Founders screen already lists every row and filters by raw state.
// This asks a different question. State is where a row sits in the
// machine; outcome is what became of the application, and the two do not
// line up - `sent`, `replied`, `closed` and `bounced` are four states and
// one outcome, while `pending_approval` is one state and two outcomes
// depending on whether the operator passed over it.
//
// So the filters are the words he used, not the enum: sent, rejected,
// skipped. The raw state travels with every row anyway, so the bucket
// never hides the truth.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApplications } from '../api/hooks'
import { outreachStateLabel } from '../labels'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

// Mirrors app/api/routers/applications.py's OUTCOMES, in the order they
// matter to someone asking "how is this going?" - what went out first,
// what is waiting on me last.
const OUTCOMES = [
  { key: 'all', label: 'All' },
  { key: 'sent', label: 'Sent' },
  { key: 'rejected', label: 'Rejected' },
  { key: 'skipped', label: 'Skipped' },
  { key: 'waiting', label: 'Waiting on you' },
  { key: 'not_started', label: 'Not started' },
]

// What each bucket means, said once at the top rather than guessed at.
const BLURB = {
  all: 'Every founder the pipeline knows about, and what became of the letter.',
  sent: 'The letter reached them. Replies and bounces are part of this — it went out.',
  rejected: 'Drafts you killed before they went out. Nobody received these.',
  skipped: 'You read these in Review and moved on without deciding.',
  waiting: 'Drafted and waiting on you — to approve, or to send.',
  not_started: 'No letter yet: still finding an address, or not drafted.',
}

const VARIANT = {
  sent: 'default',
  rejected: 'destructive',
  skipped: 'secondary',
  waiting: 'outline',
  not_started: 'outline',
}

function when(iso) {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString()
}

export default function Applications() {
  const [outcome, setOutcome] = useState('all')
  // The whole list is fetched once and filtered here: the counts on the
  // tabs have to be right whichever tab is open, and asking the server
  // per tab would mean six requests to show six numbers.
  const query = useApplications()
  const all = query.data ?? []

  const counts = OUTCOMES.reduce((acc, { key }) => {
    acc[key] = key === 'all' ? all.length : all.filter((a) => a.outcome === key).length
    return acc
  }, {})
  const rows = outcome === 'all' ? all : all.filter((a) => a.outcome === outcome)

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
        <AlertTitle>Could not load applications</AlertTitle>
        <AlertDescription>{query.error.message}</AlertDescription>
      </Alert>
    )
  }

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Applications</h1>
        <p className="text-muted-foreground text-sm">{BLURB[outcome]}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        {OUTCOMES.map(({ key, label }) => (
          <Button
            key={key}
            variant={outcome === key ? 'default' : 'outline'}
            size="sm"
            onClick={() => setOutcome(key)}
          >
            {label}
            <Badge
              variant="secondary"
              className={outcome === key ? 'bg-background/20 text-inherit' : undefined}
            >
              {counts[key]}
            </Badge>
          </Button>
        ))}
      </div>

      {rows.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Nothing here yet</EmptyTitle>
            <EmptyDescription>{BLURB[outcome]}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Card className="py-0">
          <CardContent className="px-0">
            <Table className="min-w-[60rem] table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-44">Company</TableHead>
                  <TableHead className="w-40">Founder</TableHead>
                  <TableHead>Subject</TableHead>
                  <TableHead className="w-36">Outcome</TableHead>
                  <TableHead className="w-40">Where it stands</TableHead>
                  <TableHead className="w-28">When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.outreach_id}>
                    <TableCell className="font-medium">{row.company_name ?? ''}</TableCell>
                    <TableCell>
                      <Link
                        className="underline underline-offset-4"
                        to={`/thread/${row.outreach_id}`}
                      >
                        {row.founder_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {row.subject ?? <span className="italic">no draft yet</span>}
                    </TableCell>
                    <TableCell>
                      <Badge variant={VARIANT[row.outcome] ?? 'outline'}>
                        {OUTCOMES.find((o) => o.key === row.outcome)?.label ?? row.outcome}
                      </Badge>
                    </TableCell>
                    {/* The raw state travels with the bucket: the summary
                        never hides which of the four "sent" states this
                        actually is. */}
                    <TableCell className="text-muted-foreground text-sm">
                      {outreachStateLabel(row.state)}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm whitespace-nowrap">
                      {when(row.sent_at) ?? when(row.skipped_at) ?? ''}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </>
  )
}
