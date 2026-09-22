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
import { useJobs, useStartJob, useStats } from '../api/hooks'
import JobButton from '../components/JobButton'
import Banner from '../components/Banner'

// Display order follows the pipeline's normal flow. The four states that
// are exits/exceptions rather than forward progress (unresolved,
// discarded, bounced, failed) get their own group so a healthy funnel and
// a pile of exceptions are never mixed into one number.
const FUNNEL_STATES = [
  ['new', 'New'],
  ['needs_email', 'Needs email'],
  ['needs_manual_email', 'Needs manual email'],
  ['ready_to_draft', 'Ready to draft'],
  ['pending_approval', 'Pending approval'],
  ['approved', 'Approved'],
  ['sent', 'Sent'],
  ['replied', 'Replied'],
  ['closed', 'Closed'],
]

const EXCEPTION_STATES = [
  ['unresolved', 'Unresolved'],
  ['discarded', 'Discarded'],
  ['bounced', 'Bounced'],
  ['failed', 'Failed'],
]

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
    description: 'Fetch the current YC company list and upsert it into the database.',
  },
  {
    kind: 'scrape',
    label: 'Scrape',
    group: 'pipeline',
    description: "Scrape each active company's YC page for founders, job postings, news and launches.",
  },
  {
    kind: 'resolve',
    label: 'Resolve',
    group: 'pipeline',
    description: 'Look up and verify founder email addresses against the daily verifier quota.',
  },
  {
    kind: 'draft',
    label: 'Draft',
    group: 'pipeline',
    description: 'Draft outreach messages for founders who are ready, with an advisory voice critic.',
  },
  {
    kind: 'poll-replies',
    label: 'Poll replies',
    group: 'reply-loop',
    description: 'Check the connected Gmail inbox for new replies and bounces to sent outreach.',
  },
  {
    kind: 'classify-replies',
    label: 'Classify replies',
    group: 'reply-loop',
    description:
      'Classify newly polled replies with the LLM: INTERESTED, REJECTED, REFERRAL, AUTO_REPLY, ' +
      'OUT_OF_OFFICE, or UNCLEAR. Machine replies (auto-reply, out-of-office) never end a ' +
      "follow-up sequence — only a founder's own words do.",
  },
  {
    kind: 'schedule-follow-ups',
    label: 'Schedule follow-ups',
    group: 'reply-loop',
    description: 'Schedule or close follow-up messages based on reply classifications and elapsed time.',
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

function latestJobByKind(jobs) {
  const latest = {}
  for (const job of jobs ?? []) {
    const current = latest[job.kind]
    if (!current || job.id > current.id) latest[job.kind] = job
  }
  return latest
}

function JobStatusCell({ job }) {
  if (!job) return <span className="job-status job-status-none">never run</span>

  if (ACTIVE_STATUSES.has(job.status)) {
    const progress = job.total != null ? `${job.done} / ${job.total}` : `${job.done}`
    return (
      <span className="job-status job-status-running">
        {job.status} &mdash; {progress}
      </span>
    )
  }

  if (job.status === 'failed') {
    return (
      <span className="job-status job-status-failed">
        failed: {job.error ?? 'unknown error'}
      </span>
    )
  }

  if (job.status === 'succeeded') {
    return (
      <span className="job-status job-status-succeeded">{job.summary ?? 'succeeded'}</span>
    )
  }

  return <span className="job-status">{job.status}</span>
}

function StageTable({ heading, stages, latest, startJob, onStart }) {
  return (
    <div className="stage-group">
      <h3>{heading}</h3>
      <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Stage</th>
            <th scope="col">What it does</th>
            <th scope="col">Action</th>
            <th scope="col">Last run</th>
          </tr>
        </thead>
        <tbody>
          {stages.map(({ kind, label, description }) => {
            const job = latest[kind]
            const kindRunning = job && ACTIVE_STATUSES.has(job.status)
            const startingThisKind = startJob.isPending && startJob.variables?.kind === kind
            return (
              <tr key={kind}>
                <td>{label}</td>
                <td className="stage-description">{description}</td>
                <td>
                  <JobButton
                    kind={kind}
                    label={label}
                    disabled={Boolean(kindRunning || startingThisKind)}
                    onClick={() => onStart(kind)}
                  />
                </td>
                <td>
                  <JobStatusCell job={job} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      </div>
    </div>
  )
}

export default function Pipeline() {
  const statsQuery = useStats()
  const jobsQuery = useJobs()
  const startJob = useStartJob()

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

  return (
    <div className="pipeline">
      <h1>Pipeline</h1>

      {statsQuery.isError && (
        <Banner kind="error">Could not load stats: {statsQuery.error.message}</Banner>
      )}
      {showStartError && (
        <Banner kind="error">Could not start job: {startJob.error.message}</Banner>
      )}
      {gmailAuthExpired(latest) && (
        <Banner kind="error">
          Gmail authorisation expired &mdash; re-run <code>python -m app.cli gmail-auth</code>,
          then start Poll replies again.
        </Banner>
      )}

      <section className="funnel">
        <h2>Funnel</h2>
        <div className="table-scroll">
        <table>
          <tbody>
            {FUNNEL_STATES.map(([key, label]) => (
              <tr key={key} className={key === 'needs_manual_email' ? 'is-action-queue' : undefined}>
                <th scope="row">{label}</th>
                <td className="count">{counts[key] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>

        <h3>Exceptions</h3>
        <div className="table-scroll">
        <table>
          <tbody>
            {EXCEPTION_STATES.map(([key, label]) => (
              <tr key={key}>
                <th scope="row">{label}</th>
                <td className="count">{counts[key] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </section>

      <section className="stages">
        <h2>Stages</h2>
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
      </section>
    </div>
  )
}
