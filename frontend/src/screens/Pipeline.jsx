// Task 15: the `/` screen. Funnel counts by raw outreach state (T3-1 -
// every state is shown, `needs_manual_email` gets its own visible row
// since it's an action queue, not folded into "new"), one button per
// pipeline stage that's actually wired on the backend today
// (`JOB_KINDS` in app/api/jobs.py: sync, scrape, resolve, draft -
// poll-replies/classify-replies/schedule-follow-ups aren't registered
// yet), live progress while a stage runs, and the last job per kind shown
// even when nothing is running right now - a failed job an hour ago with
// its error is more useful than a screen that goes quiet.
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

// JOB_KINDS from app/api/jobs.py - only stages the backend actually
// registers for POST /jobs/{kind} today. Starting any other kind 404s.
const STAGES = [
  ['sync', 'Sync'],
  ['scrape', 'Scrape'],
  ['resolve', 'Resolve'],
  ['draft', 'Draft'],
]

const ACTIVE_STATUSES = new Set(['queued', 'running'])

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

      <section className="funnel">
        <h2>Funnel</h2>
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

        <h3>Exceptions</h3>
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
      </section>

      <section className="stages">
        <h2>Stages</h2>
        <table>
          <thead>
            <tr>
              <th scope="col">Stage</th>
              <th scope="col">Action</th>
              <th scope="col">Last run</th>
            </tr>
          </thead>
          <tbody>
            {STAGES.map(([kind, label]) => {
              const job = latest[kind]
              const kindRunning = job && ACTIVE_STATUSES.has(job.status)
              const startingThisKind =
                startJob.isPending && startJob.variables?.kind === kind
              return (
                <tr key={kind}>
                  <td>{label}</td>
                  <td>
                    <JobButton
                      kind={kind}
                      label={kind}
                      disabled={Boolean(kindRunning || startingThisKind)}
                      onClick={() => handleStart(kind)}
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
      </section>
    </div>
  )
}
