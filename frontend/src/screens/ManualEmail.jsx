// Task 17: the Manual email screen - the NEEDS_MANUAL_EMAIL founders
// (backend/app/api/routers/manual.py's `GET /api/manual`, 65 on the live
// database). Founder, company, domain, LinkedIn link, the candidates
// already tried with their verdicts, the published-email hint when
// `resolve` found one, a text field for a hand-typed address, and a
// destructive-ish "give up" control (requires confirmation) that moves
// the founder to UNRESOLVED.
import { useState } from 'react'
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react'
import { useManual, useSetEmail } from '../api/hooks'
import { useSortedRows } from '@/lib/sort'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

// V2 (T18 fix wave 2): a founder with several guessed-and-rejected
// candidates (7, for the Orangewood founders) rendered as an
// always-open list made every row ~270px tall, pushing the one
// actionable column ("Set email") far enough right that it clipped.
// Collapsed by default, the row is one line ("N tried") unless the
// reviewer actually wants to see what was already attempted.
function CandidateList({ candidates }) {
  if (!candidates || candidates.length === 0) {
    return <span className="text-muted-foreground text-sm">none tried yet</span>
  }
  return (
    <Collapsible>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="h-7 px-2">
          {candidates.length} tried
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="text-muted-foreground mt-1 space-y-0.5 text-xs">
          {candidates.map((candidate) => (
            <li key={candidate.address}>
              {candidate.address} &mdash; {candidate.verdict ?? 'unknown'}
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

// One row's hand-typed-address form + give-up control. Local state
// (`address`) is never cleared on a failed submit (a 422 from the
// validator) - the operator's typed text must survive exactly as they
// left it, per the brief.
function ManualEmailForm({ item }) {
  const [address, setAddress] = useState('')
  const [error, setError] = useState(null)
  const setEmail = useSetEmail()

  function submit(event) {
    event.preventDefault()
    setError(null)
    setEmail.mutate(
      { id: item.outreach_id, address },
      { onError: (err) => setError(err.errors ? err.errors.join(', ') : err.message) },
    )
  }

  function giveUp() {
    // Destructive-ish (moves the founder to UNRESOLVED, out of the
    // active pipeline) - requires an explicit confirm before firing.
    const ok = window.confirm(
      `Give up on ${item.founder_name}? This marks the founder UNRESOLVED.`,
    )
    if (!ok) return
    setError(null)
    setEmail.mutate({ id: item.outreach_id, giveUp: true })
  }

  return (
    <form className="flex flex-wrap items-center gap-1.5" onSubmit={submit}>
      {/* No visible label: the column header already says "Set email",
          and a caption stacked above every input pushed each row to
          three lines. The full, unique name stays on the input's
          aria-label for screen readers. */}
      <Input
        className="w-40"
        aria-label={`Address for ${item.founder_name}`}
        placeholder="name@company.com"
        value={address}
        onChange={(event) => setAddress(event.target.value)}
      />
      <Button type="submit" size="sm" disabled={setEmail.isPending}>
        Submit
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={giveUp}
        disabled={setEmail.isPending}
      >
        Give up
      </Button>
      {error && (
        <p role="alert" className="text-destructive basis-full text-xs">
          {error}
        </p>
      )}
    </form>
  )
}

const columns = [
  { key: 'founder_name', label: 'Founder', accessor: (row) => row.founder_name },
  { key: 'company_name', label: 'Company', accessor: (row) => row.company_name ?? '' },
  { key: 'domain', label: 'Domain', accessor: (row) => row.domain ?? '' },
  { key: 'linkedin', label: 'LinkedIn', sortable: false },
  { key: 'candidates', label: 'Candidates tried', sortable: false },
  { key: 'published_hint', label: 'Published hint', sortable: false },
  { key: 'set_email', label: 'Set email', sortable: false },
]

// The one column that asks something of you is the widest; the rest are
// held down so the whole row fits without a sideways scroll.
const COLUMN_WIDTHS = {
  founder_name: 'w-36',
  company_name: 'w-36',
  domain: 'w-32',
  linkedin: 'w-20',
  candidates: 'w-32',
  published_hint: 'w-44',
  set_email: 'w-80',
}

function SortIcon({ active, dir }) {
  if (!active) return <ChevronsUpDown className="text-muted-foreground/60 size-3.5" />
  return dir === 'asc' ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />
}

export default function ManualEmail() {
  const manualQuery = useManual()
  const items = manualQuery.data ?? []

  const sorted = useSortedRows({
    columns,
    rows: items,
    getRowKey: (row) => row.outreach_id,
    defaultSortKey: 'founder_name',
  })

  if (manualQuery.isLoading) {
    return (
      <div className="grid gap-2">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    )
  }

  if (manualQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load manual queue</AlertTitle>
        <AlertDescription>{manualQuery.error.message}</AlertDescription>
      </Alert>
    )
  }

  if (items.length === 0) {
    return (
      <>
        <h1 className="text-2xl font-semibold tracking-tight">Missing addresses</h1>
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Every founder in the pipeline has an address</EmptyTitle>
            <EmptyDescription>Nothing to do here.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </>
    )
  }

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Missing addresses</h1>
        <p className="text-muted-foreground text-sm">
          The verifier could not find a working address for these founders. Add one yourself, or
          give up on a founder to take them out of the queue.
        </p>
      </div>

      <Card className="py-0">
        <CardContent className="px-0">
          {/* Fixed layout, or the column widths above are only
              hints and the auto algorithm hands the slack to the
              first column and pushes the last one off the card.
              The minimum keeps it legible on a narrow window, where
              the table scrolls sideways instead of squeezing. */}
          <Table className="min-w-[70rem] table-fixed">
            <TableHeader>
              <TableRow>
                {columns.map((column) => (
                  <TableHead
                    key={column.key}
                    aria-sort={sorted.ariaSort(column)}
                    className={COLUMN_WIDTHS[column.key]}
                  >
                    {column.sortable === false ? (
                      column.label
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="-ml-2 h-7 px-2"
                        onClick={() => sorted.toggleSort(column)}
                      >
                        {column.label}
                        <SortIcon active={sorted.sortKey === column.key} dir={sorted.sortDir} />
                      </Button>
                    )}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.rows.map((row) => (
                <TableRow key={row.outreach_id}>
                  <TableCell className="font-medium whitespace-nowrap">
                    {row.founder_name}
                  </TableCell>
                  <TableCell>{row.company_name ?? ''}</TableCell>
                  <TableCell className="text-muted-foreground">{row.domain ?? ''}</TableCell>
                  <TableCell>
                    {row.linkedin_url ? (
                      <a
                        className="underline underline-offset-4"
                        href={row.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        LinkedIn
                      </a>
                    ) : (
                      <span className="text-muted-foreground">&mdash;</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <CandidateList candidates={row.candidates} />
                  </TableCell>
                  <TableCell>
                    {row.published_hint ? (
                      <span className="text-sm">{row.published_hint}</span>
                    ) : (
                      <span className="text-muted-foreground">&mdash;</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <ManualEmailForm item={row} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  )
}
