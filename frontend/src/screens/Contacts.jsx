// Task 17: the Contacts screen - every company/founder pair
// (backend/app/api/routers/contacts.py's `GET /api/contacts`), filterable
// by state/hiring/batch/email_status, as a plain dense table (no card
// grid).
//
// D19 (restated in the router's own docstring): `hiring` is a VIEW
// control, never a gate - a non-hiring company is listed by default, same
// as every other row. The filter defaults to "all" (no `hiring` param
// sent at all), so `useContacts({})`'s first request returns everyone;
// only selecting "Hiring only" narrows it.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react'
import { useContacts } from '../api/hooks'
import { outreachStateLabel } from '../labels'
import { useSortedRows } from '@/lib/sort'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

// OutreachState values (app/domain/states.py), mirrored by hand like
// Pipeline.jsx's FUNNEL_STATES - there's no endpoint to discover them at
// runtime. Filter order follows the pipeline's flow; the words come from
// the shared vocabulary so a row reads the same here as on the Pipeline.
const OUTREACH_STATES = [
  'new',
  'needs_email',
  'needs_manual_email',
  'ready_to_draft',
  'pending_approval',
  'approved',
  'sent',
  'replied',
  'closed',
  'discarded',
  'bounced',
  'unresolved',
  'failed',
]

// shadcn's Select cannot carry an empty-string value (Radix reserves it
// for "cleared"), so "all" is the sentinel and the filter object below
// is what decides whether a param is sent at all.
const ALL = 'all'

const columns = [
  { key: 'company_name', label: 'Company', accessor: (row) => row.company_name ?? '' },
  { key: 'founder_name', label: 'Founder', accessor: (row) => row.founder_name },
  { key: 'domain', label: 'Domain', accessor: (row) => row.domain ?? '' },
  { key: 'state', label: 'Where it stands', accessor: (row) => outreachStateLabel(row.state) },
  { key: 'batch', label: 'Batch', accessor: (row) => row.batch ?? '' },
  { key: 'team_size', label: 'Team size', accessor: (row) => row.team_size ?? 0 },
  { key: 'is_hiring', label: 'Hiring', accessor: (row) => (row.is_hiring ? 'yes' : 'no') },
  {
    key: 'email_status',
    label: 'Address',
    accessor: (row) => (row.email_status === 'resolved' ? 'found' : 'not found'),
  },
]

// Widths so all eight columns fit the card rather than running off its
// right edge - the founders table is the widest thing in the app.
const COLUMN_WIDTHS = {
  company_name: 'w-44',
  founder_name: 'w-40',
  domain: 'w-40',
  state: 'w-40',
  batch: 'w-32',
  team_size: 'w-28',
  is_hiring: 'w-24',
  email_status: 'w-32',
}

function SortIcon({ active, dir }) {
  if (!active) return <ChevronsUpDown className="text-muted-foreground/60 size-3.5" />
  return dir === 'asc' ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />
}

export default function Contacts() {
  const [state, setState] = useState(ALL)
  const [hiring, setHiring] = useState(ALL)
  const [batch, setBatch] = useState('')
  const [emailStatus, setEmailStatus] = useState(ALL)

  const filters = {}
  if (state !== ALL) filters.state = state
  if (hiring !== ALL) filters.hiring = hiring === 'true'
  if (batch !== '') filters.batch = batch
  if (emailStatus !== ALL) filters.email_status = emailStatus

  const contactsQuery = useContacts(filters)
  const contacts = contactsQuery.data ?? []

  const sorted = useSortedRows({
    columns,
    rows: contacts,
    getRowKey: (row) => row.outreach_id,
    defaultSortKey: 'company_name',
  })

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Founders</h1>
        <p className="text-muted-foreground text-sm">
          Every founder the pipeline knows about, and where each one stands.
        </p>
      </div>

      <form
        className="flex flex-wrap items-end gap-4"
        onSubmit={(event) => event.preventDefault()}
      >
        <div className="grid gap-2">
          <Label htmlFor="filter-state">Where it stands</Label>
          <Select value={state} onValueChange={setState}>
            <SelectTrigger id="filter-state" className="w-52">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All</SelectItem>
              {OUTREACH_STATES.map((value) => (
                <SelectItem key={value} value={value}>
                  {outreachStateLabel(value)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="filter-hiring">Hiring</Label>
          <Select value={hiring} onValueChange={setHiring}>
            <SelectTrigger id="filter-hiring" className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All companies</SelectItem>
              <SelectItem value="true">Hiring only</SelectItem>
              <SelectItem value="false">Not hiring only</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="filter-batch">Batch</Label>
          <Input
            id="filter-batch"
            className="w-40"
            value={batch}
            onChange={(event) => setBatch(event.target.value)}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="filter-address">Address</Label>
          <Select value={emailStatus} onValueChange={setEmailStatus}>
            <SelectTrigger id="filter-address" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All</SelectItem>
              <SelectItem value="resolved">Found</SelectItem>
              <SelectItem value="unresolved">Not found</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </form>

      {contactsQuery.isLoading && (
        <div className="grid gap-2">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      )}

      {contactsQuery.isError && (
        <Alert variant="destructive">
          <AlertTitle>Could not load contacts</AlertTitle>
          <AlertDescription>{contactsQuery.error.message}</AlertDescription>
        </Alert>
      )}

      {!contactsQuery.isLoading &&
        !contactsQuery.isError &&
        (contacts.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyTitle>No founders match these filters</EmptyTitle>
              <EmptyDescription>Widen one of them to see more.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
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
                        <Button
                          variant="ghost"
                          size="sm"
                          className="-ml-2 h-7 px-2"
                          onClick={() => sorted.toggleSort(column)}
                        >
                          {column.label}
                          <SortIcon
                            active={sorted.sortKey === column.key}
                            dir={sorted.sortDir}
                          />
                        </Button>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sorted.rows.map((row) => (
                    <TableRow key={row.outreach_id}>
                      <TableCell className="font-medium">{row.company_name ?? ''}</TableCell>
                      <TableCell>
                        <Link className="underline underline-offset-4" to={`/thread/${row.outreach_id}`}>
                          {row.founder_name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{row.domain ?? ''}</TableCell>
                      <TableCell>{outreachStateLabel(row.state)}</TableCell>
                      <TableCell className="whitespace-nowrap">{row.batch ?? ''}</TableCell>
                      <TableCell className="tabular-nums">{row.team_size ?? 0}</TableCell>
                      <TableCell>{row.is_hiring ? 'yes' : 'no'}</TableCell>
                      <TableCell>
                        {row.email_status === 'resolved' ? (
                          <Badge variant="secondary">found</Badge>
                        ) : (
                          <Badge variant="outline">not found</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
    </>
  )
}
