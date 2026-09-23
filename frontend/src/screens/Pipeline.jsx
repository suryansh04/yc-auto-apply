// Task 15 (+ fix round 1, after 15a registered the remaining three
// stages on the backend): the `/` screen. Funnel counts by raw outreach
// state (T3-1 - every state is shown, `needs_manual_email` gets its own
// visible row since it's an action queue, not folded into "new"), one
// button per pipeline stage the backend registers (`JOB_KINDS` in
// app/api/jobs.py - all seven, mirrored in `STAGES` below and pinned by
// `Pipeline.test.jsx`'s registry test so this can't silently drift again
// the way it did between Task 15 and 15a), live progress while a stage
// runs, and the last job per kind shown even when nothing is running
// right now - a failed job an hour ago with its error is more useful
// than a screen that goes quiet.
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Send } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from 'recharts'
import { useJobs, useSend, useStartJob, useStats } from '../api/hooks'
import { outreachStateLabel } from '../labels'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { Spinner } from '@/components/ui/spinner'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

// Display order follows the pipeline's normal flow. The four states that
// are exits/exceptions rather than forward progress (unresolved,
// discarded, bounced, failed) get their own group so a healthy funnel and
// a pile of exceptions are never mixed into one number.
const FUNNEL_STATES = [
  'new',
  'needs_email',
  'needs_manual_email',
  'ready_to_draft',
  'pending_approval',
  'approved',
  'sent',
  'replied',
  'closed',
]

const EXCEPTION_STATES = ['unresolved', 'discarded', 'bounced', 'failed']

// JOB_KINDS from app/api/jobs.py, mirrored exactly - there's no endpoint
// to derive this list from, so this array (and the registry test that
// pins it) IS the contract. Order and membership must match the
// backend's `JOB_KINDS` dict / `test_job_kinds_has_exactly_the_seven_registered_stages`
// or a registered stage silently has no way to start from the UI - the
// exact gap the fix-round-1 review found (Task 15 shipped with only the
// first four; 15a registered the other three on the backend without a
// matching frontend edit).
//
// `group` splits the table into the outreach pipeline proper (find ->
// scrape -> resolve emails -> draft) and the reply loop that runs after
// a message is sent (poll inbox -> classify -> schedule follow-ups).
const STAGES = [
  {
    kind: 'sync',
    label: 'Sync',
    group: 'pipeline',
    description: 'Pull the current YC company list and add anything new.',
  },
  {
    kind: 'scrape',
    label: 'Scrape',
    group: 'pipeline',
    description: "Read each company's YC page for founders, jobs, news and launch posts.",
  },
  {
    kind: 'resolve',
    label: 'Resolve',
    group: 'pipeline',
    description: "Find and verify founder email addresses, within the day's free quota.",
  },
  {
    kind: 'draft',
    label: 'Draft',
    group: 'pipeline',
    description: 'Write a letter to every founder who has an address and research to cite.',
  },
  {
    kind: 'poll-replies',
    label: 'Poll replies',
    group: 'reply-loop',
    description: 'Check Gmail for replies and bounces to letters you have sent.',
  },
  {
    kind: 'classify-replies',
    label: 'Classify replies',
    group: 'reply-loop',
    description:
      'Sort new replies: interested, not interested, a referral, or an automatic ' +
      'reply. An out-of-office never counts as an answer, so follow-ups keep going ' +
      'until a founder actually writes back.',
  },
  {
    kind: 'schedule-follow-ups',
    label: 'Schedule follow-ups',
    group: 'reply-loop',
    description: 'Line up follow-ups for silent threads, and close the ones that are done.',
  },
]

const STAGE_GROUPS = [
  ['pipeline', 'Outreach pipeline'],
  ['reply-loop', 'Reply loop'],
]

const ACTIVE_STATUSES = new Set(['queued', 'running'])

// Final fix wave (F2, spec §9 "Gmail 401 during polling -> the job fails
// with 're-run gmail-auth', which the UI surfaces as a banner"). The
// backend records only the exception's TYPE NAME on a failed job, so the
// banner keys on exactly that string for exactly the poll-replies kind -
// any other failed job keeps its ordinary "failed: X" cell.
export const GMAIL_AUTH_REQUIRED = 'GmailAuthRequired'

export function gmailAuthExpired(latest) {
  const job = latest?.['poll-replies']
  return Boolean(job && job.status === 'failed' && job.error === GMAIL_AUTH_REQUIRED)
}

// The one scale both funnel charts are drawn on. Scaling each group to
// its own largest bar made "Discarded 1" as wide as "Not started 162" -
// the two are read as one picture of the pipeline, so a bar's length has
// to mean the same thing in both. Exported because the charts render
// nothing without a layout engine, so this is where the property is
// pinned.
export function funnelDomainMax(counts) {
  return Math.max(
    ...[...FUNNEL_STATES, ...EXCEPTION_STATES].map((state) => counts?.[state] ?? 0),
    1,
  )
}

// `POST /api/send` answers with five counts. Saying only "sent 3" hides
// the four that were capped or need a manual check, which are the ones
// the operator has to do something about.
export function describeSendResult(result) {
  const parts = []
  if (result.skipped_cap) parts.push(`${result.skipped_cap} held back by the daily cap`)
  if (result.needs_manual_check) parts.push(`${result.needs_manual_check} need a manual check`)
  if (result.skipped_not_reservable) parts.push(`${result.skipped_not_reservable} were already taken`)
  if (result.errors) parts.push(`${result.errors} could not be sent`)
  if (parts.length === 0) return 'Nothing else was waiting.'
  return `${parts.join(', ')}.`
}

function latestJobByKind(jobs) {
  const latest = {}
  for (const job of jobs ?? []) {
    const current = latest[job.kind]
    if (!current || job.id > current.id) latest[job.kind] = job
  }
  return latest
}

// One series, one hue: the bar's LENGTH is the value, so colour carries
// nothing and never needs a legend. `chart-3` rather than the primary,
// because the primary is what buttons wear.
const FUNNEL_CONFIG = {
  value: { label: 'Founders', color: 'var(--chart-3)' },
}

function JobStatusCell({ job }) {
  if (!job) return <Badge variant="outline">never run</Badge>

  if (ACTIVE_STATUSES.has(job.status)) {
    const progress = job.total != null ? `${job.done} / ${job.total}` : `${job.done}`
    return (
      <Badge variant="secondary">
        {job.status} &mdash; {progress}
      </Badge>
    )
  }

  if (job.status === 'failed') {
    // 2026-09-23: `job.error` used to be an exception type name and
    // nothing else, which fits a badge. A stage may now answer with a
    // sentence written for the operator (`app.application.errors`), and
    // a sentence in a badge is a sentence nobody can read - so anything
    // longer than a type name gets room to wrap.
    const reason = job.error ?? 'unknown error'
    if (reason.length <= 32 && !reason.includes(' ')) {
      return <Badge variant="destructive">failed: {reason}</Badge>
    }
    return (
      <div className="flex max-w-prose flex-wrap items-start gap-2">
        <Badge variant="destructive">failed</Badge>
        <span className="text-destructive text-sm leading-snug">{reason}</span>
      </div>
    )
  }

  if (job.status === 'succeeded') {
    return <span className="text-muted-foreground text-sm">{job.summary ?? 'succeeded'}</span>
  }

  return <Badge variant="outline">{job.status}</Badge>
}

// Rows stay in pipeline order rather than sorted by size - the order is
// the story - and every bar keeps its exact count, because that is what
// the table this replaced was read for. A stage with nobody in it keeps
// its row: "Sent 0" is a fact about the pipeline.
function funnelRows(counts, states) {
  return states.map((state) => ({
    state,
    label: outreachStateLabel(state),
    value: counts[state] ?? 0,
  }))
}

function StageTable({ heading, stages, latest, startJob, onStart }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{heading}</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-36">Stage</TableHead>
              <TableHead>What it does</TableHead>
              <TableHead className="w-40">Action</TableHead>
              <TableHead className="w-64">Last run</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {stages.map(({ kind, label, description }) => {
              const job = latest[kind]
              const kindRunning = job && ACTIVE_STATUSES.has(job.status)
              const startingThisKind = startJob.isPending && startJob.variables?.kind === kind
              return (
                <TableRow key={kind}>
                  <TableCell className="font-medium">{label}</TableCell>
                  <TableCell className="text-muted-foreground">{description}</TableCell>
                  <TableCell>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={Boolean(kindRunning || startingThisKind)}
                      onClick={() => onStart(kind)}
                    >
                      {label}
                    </Button>
                  </TableCell>
                  <TableCell>
                    <JobStatusCell job={job} />
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function FunnelCard({ title, description, counts, states, domainMax, action }) {
  const rows = funnelRows(counts, states)
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
        {action && <CardAction>{action}</CardAction>}
      </CardHeader>
      <CardContent>
        <ChartContainer
          config={FUNNEL_CONFIG}
          className="aspect-auto w-full"
          style={{ height: `${Math.max(rows.length * 36, 140)}px` }}
        >
          {/* A partial `margin` replaces recharts' defaults rather than
              merging with them, so all four sides are given. */}
          <BarChart
            accessibilityLayer
            data={rows}
            layout="vertical"
            margin={{ top: 4, right: 44, bottom: 4, left: 4 }}
          >
            <CartesianGrid horizontal={false} />
            <YAxis
              dataKey="label"
              type="category"
              tickLine={false}
              axisLine={false}
              width={132}
              tickMargin={6}
            />
            {/* Both charts share one domain: they are read as one picture
                of the pipeline, so a bar's length has to mean the same
                thing in both. Scaling each to its own largest bar made
                "Discarded 1" as wide as "Not started 162". */}
            <XAxis type="number" hide domain={[0, domainMax]} />
            <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
            {/* No entry animation: bars sliding out on every poll of
                /api/stats is motion that answers nothing. */}
            <Bar
              dataKey="value"
              fill="var(--color-value)"
              radius={[0, 4, 4, 0]}
              barSize={16}
              isAnimationActive={false}
              /* A zero-value bar draws no rectangle, and with no
                 rectangle recharts has nothing to hang the count on -
                 so an empty stage lost its "0", which is a fact about
                 the pipeline worth keeping. A 2px floor gives every row
                 something to label. */
              minPointSize={2}
            >
              <LabelList
                dataKey="value"
                position="right"
                offset={8}
                className="fill-foreground"
                fontSize={12}
              />
            </Bar>
          </BarChart>
        </ChartContainer>

        {/* The chart is a picture. This is the same data as text, so
            every count is reachable without reading a bar length - by a
            screen reader, and by anything else that cannot do layout. */}
        <ul className="sr-only">
          {rows.map(({ state, label, value }) => (
            <li key={state}>
              <span>{label}</span> <span>{value}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

export default function Pipeline() {
  const statsQuery = useStats()
  const jobsQuery = useJobs()
  const startJob = useStartJob()
  const send = useSend()

  const counts = statsQuery.data?.counts ?? {}
  const latest = useMemo(() => latestJobByKind(jobsQuery.data), [jobsQuery.data])

  function handleStart(kind) {
    startJob.mutate(
      { kind },
      {
        // A 409 means "already running", carrying the running job's id
        // (ApiError#job_id) - not a failure to display. Refetch /api/jobs
        // so the button flips to disabled and the running job shows up,
        // instead of leaving the user staring at a stale "start" button.
        onError: (err) => {
          if (err?.status === 409) jobsQuery.refetch()
        },
      },
    )
  }

  // A genuine start failure (anything other than the 409 "already
  // running" case) is shown, not swallowed.
  const showStartError = startJob.isError && startJob.error?.status !== 409

  const domainMax = funnelDomainMax(counts)
  const waiting = counts.pending_approval ?? 0
  const missingAddresses = counts.needs_manual_email ?? 0
  const approvedNotSent = counts.approved ?? 0

  function handleSend() {
    // Sending is the one irreversible thing this app does, so it asks
    // first and says exactly how many letters are about to leave.
    const ok = window.confirm(
      `Send ${approvedNotSent} approved ${approvedNotSent === 1 ? 'letter' : 'letters'} now? ` +
        'This cannot be undone.',
    )
    if (ok) send.mutate()
  }

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Pipeline</h1>
        <p className="text-muted-foreground text-sm">
          Where every founder stands, and the stages you can run by hand.
        </p>
      </div>

      {statsQuery.isError && (
        <Alert variant="destructive">
          <AlertTitle>Could not load stats</AlertTitle>
          <AlertDescription>{statsQuery.error.message}</AlertDescription>
        </Alert>
      )}
      {showStartError && (
        <Alert variant="destructive">
          <AlertTitle>Could not start job</AlertTitle>
          <AlertDescription>{startJob.error.message}</AlertDescription>
        </Alert>
      )}
      {gmailAuthExpired(latest) && (
        <Alert variant="destructive">
          <AlertTitle>Gmail authorisation expired</AlertTitle>
          <AlertDescription>
            Re-run{' '}
            <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">
              python -m app.cli gmail-auth
            </code>
            , then start Poll replies again.
          </AlertDescription>
        </Alert>
      )}

      {/* Operator finding (2026-09-22): "clicking approve does nothing,
          not sending the email". Approve was working - it moves the
          letter to approved - but approving and sending are two steps on
          purpose, and there was no way to do the second one from the UI
          at all. This is it. */}
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader>
            <CardDescription>
              {waiting === 1 ? 'Letter waiting for you' : 'Letters waiting for you'}
            </CardDescription>
            <CardTitle className="text-4xl tabular-nums">{waiting}</CardTitle>
            <CardAction>
              <Button asChild size="sm">
                <Link to="/queue">Start reviewing</Link>
              </Button>
            </CardAction>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>
              {approvedNotSent === 1 ? 'Letter approved, not sent' : 'Letters approved, not sent'}
            </CardDescription>
            <CardTitle className="text-4xl tabular-nums">{approvedNotSent}</CardTitle>
            <CardAction>
              <Button asChild size="sm" disabled={approvedNotSent === 0}>
                <Link to="/approved">
                  <Send />
                  Review and send
                </Link>
              </Button>
            </CardAction>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader>
            <CardDescription>Founders still need an address</CardDescription>
            <CardTitle className="text-4xl tabular-nums">{missingAddresses}</CardTitle>
            <CardAction>
              <Button asChild size="sm" variant="outline">
                <Link to="/manual">Add addresses</Link>
              </Button>
            </CardAction>
          </CardHeader>
        </Card>
      </div>

      {send.isSuccess && (
        <Alert>
          <AlertTitle>
            {send.data.sent === 1 ? '1 letter sent' : `${send.data.sent} letters sent`}
          </AlertTitle>
          <AlertDescription>
            {describeSendResult(send.data)}
          </AlertDescription>
        </Alert>
      )}
      {send.isError && (
        <Alert variant="destructive">
          <AlertTitle>Could not send</AlertTitle>
          <AlertDescription>{send.error.message}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <FunnelCard
          title="Everyone else"
          description="Founders at each stage of the pipeline, in order."
          counts={counts}
          states={FUNNEL_STATES}
          domainMax={domainMax}
        />
        <FunnelCard
          title="Stuck or finished"
          description="Exits and exceptions, on the same scale as the funnel."
          counts={counts}
          states={EXCEPTION_STATES}
          domainMax={domainMax}
        />
      </div>

      <div className="grid gap-4">
        <h2 className="text-lg font-semibold tracking-tight">Run a stage</h2>
        {STAGE_GROUPS.map(([groupKey, heading]) => (
          <StageTable
            key={groupKey}
            heading={heading}
            stages={STAGES.filter((s) => s.group === groupKey)}
            latest={latest}
            startJob={startJob}
            onStart={handleStart}
          />
        ))}
      </div>
    </>
  )
}
