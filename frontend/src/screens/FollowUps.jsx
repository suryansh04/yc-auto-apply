// Follow-ups (operator request 2026-10-16): "replying or following up the
// already sent mail which havent received any replies ... give a
// recommendation to what to send in the followup mail".
//
// Left: every sent email still waiting on a reply, with when its
// follow-up is due. Right: the email he sent, three suggested follow-ups,
// a box to edit the one he picks, Approve, then Send. A follow-up goes
// out as a reply in the same Gmail thread, without the resume, and never
// without his approval.
import { useEffect, useState } from 'react'
import { MailCheck, RefreshCw, SendHorizontal, Sparkles } from 'lucide-react'
import {
  useApproveFollowUp, useFollowUps, useSend, useStartJob, useSuggestFollowUps,
} from '../api/hooks'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'

const STATUS = {
  approved: { label: 'Approved, not sent', variant: 'default' },
  due: { label: 'Due', variant: 'destructive' },
  waiting: { label: 'Waiting', variant: 'outline' },
  done: { label: 'Both sent', variant: 'secondary' },
}

const STYLE = {
  nudge: 'Bring your question back',
  new_angle: 'Something new',
  last_note: 'Last note',
}

function day(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString()
}

function daysAgo(iso) {
  if (!iso) return ''
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  return days <= 0 ? 'today' : `${days} day${days === 1 ? '' : 's'} ago`
}

function Detail({ item }) {
  const [draft, setDraft] = useState(item.approved_body ?? '')
  const [shown, setShown] = useState([])
  const [error, setError] = useState(null)
  const suggest = useSuggestFollowUps({
    onSuccess: (data) => {
      const bodies = (data?.options ?? []).map((o) => o.body)
      if (bodies.length) setShown((before) => [...before, ...bodies])
    },
  })
  const approve = useApproveFollowUp({ onError: (e) => setError(e.message) })
  const send = useSend()
  const options = suggest.data?.options ?? []
  const next = item.follow_ups_sent + 1

  useEffect(() => { setDraft(item.approved_body ?? '') }, [item.outreach_id, item.approved_body])

  return (
    <Card>
      <CardHeader>
        <CardTitle>{item.founder_name} at {item.company_name}</CardTitle>
        <CardDescription>
          {item.to} · last email {daysAgo(item.last_sent_at)}
          {item.due_at && item.status !== 'approved' && ` · follow-up ${next} due ${day(item.due_at)}`}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <details className="rounded-md border p-3">
          <summary className="cursor-pointer text-sm font-medium">
            What you sent: {item.subject}
          </summary>
          {item.sent.map((s) => (
            <div key={s.step} className="mt-3 grid gap-1">
              <span className="text-muted-foreground text-xs">
                {s.step === 0 ? 'First email' : `Follow-up ${s.step}`} · {day(s.sent_at)}
              </span>
              <p className="text-sm whitespace-pre-line">{s.body}</p>
            </div>
          ))}
        </details>

        {item.status === 'approved' ? (
          <div className="grid gap-2">
            <p className="text-sm font-medium">Follow-up {item.follow_ups_sent + 1}, approved</p>
            <p className="bg-muted/50 rounded-md p-3 text-sm whitespace-pre-line">{item.approved_body}</p>
            <p className="text-muted-foreground text-xs">
              Goes as a reply in the same thread, subject "Re: {item.subject}", without the resume.
            </p>
            <Button className="w-fit" disabled={send.isPending}
                    onClick={() => send.mutate({ outreachId: item.outreach_id })}>
              {send.isPending ? <Spinner /> : <SendHorizontal />} Send now
            </Button>
            {send.isError && <p className="text-destructive text-xs">{send.error.message}</p>}
            {send.data && send.data.sent === 0 && (
              <p className="text-destructive text-xs">Not sent - check the Approved screen for why.</p>
            )}
          </div>
        ) : item.status === 'done' ? (
          <p className="text-muted-foreground text-sm">
            Both follow-ups have gone out. The thread closes when the scheduler next runs.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" className="w-fit" disabled={suggest.isPending}
                      onClick={() => suggest.mutate({ id: item.outreach_id, avoid: shown })}>
                {suggest.isPending ? <Spinner /> : options.length ? <RefreshCw /> : <Sparkles />}
                {options.length ? 'More suggestions' : `Suggest follow-up ${next}`}
              </Button>
              {suggest.data?.unavailable && !suggest.isPending && (
                <span className="text-muted-foreground text-xs">{suggest.data.unavailable}</span>
              )}
              {suggest.isError && <span className="text-destructive text-xs">{suggest.error.message}</span>}
            </div>
            {suggest.data?.why && <p className="text-muted-foreground text-xs">{suggest.data.why}</p>}
            {options.length > 0 && (
              <ul aria-label="Suggested follow-ups" className="grid gap-2">
                {options.map((o) => (
                  <li key={o.body} className="grid gap-1.5 rounded-md border border-amber-400/70 bg-amber-50 p-3 dark:border-amber-500/40 dark:bg-amber-950/40">
                    <span className="text-muted-foreground text-xs font-medium">{STYLE[o.style] ?? o.style}</span>
                    <p className="text-sm whitespace-pre-line">{o.body}</p>
                    <Button size="sm" variant="secondary" className="w-fit" onClick={() => setDraft(o.body)}>
                      Use this
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <div className="grid gap-2">
              <label htmlFor="followup-body" className="text-sm font-medium">
                Your follow-up (the greeting and your signature are added for you)
              </label>
              <Textarea id="followup-body" rows={5} value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        placeholder="Pick a suggestion above, or write your own." />
              <Button className="w-fit" disabled={!draft.trim() || approve.isPending}
                      onClick={() => { setError(null); approve.mutate({ id: item.outreach_id, body: draft }) }}>
                {approve.isPending ? <Spinner /> : <MailCheck />} Approve follow-up
              </Button>
              {error && <p className="text-destructive text-xs" role="alert">{error}</p>}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export default function FollowUps() {
  const query = useFollowUps()
  const startJob = useStartJob()
  const [selected, setSelected] = useState(null)
  const items = query.data ?? []
  const current = items.find((i) => i.outreach_id === selected) ?? items[0]

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Follow-ups</h1>
          <p className="text-muted-foreground text-sm">
            Sent emails with no reply yet. Follow-up 1 is due 5 days after your email, follow-up 2 a week after that.
          </p>
        </div>
        <Button variant="outline" disabled={startJob.isPending}
                onClick={() => startJob.mutate({ kind: 'poll-replies' })}>
          <RefreshCw /> Check inbox for replies
        </Button>
      </div>
      <Alert>
        <AlertTitle>Check for replies first</AlertTitle>
        <AlertDescription>
          A thread leaves this list once a reply is found in your inbox. Run the check before following up,
          so you never nudge someone who already answered.
        </AlertDescription>
      </Alert>

      {query.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : query.isError ? (
        <Alert variant="destructive"><AlertDescription>{query.error.message}</AlertDescription></Alert>
      ) : items.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Nothing to follow up</EmptyTitle>
            <EmptyDescription>Every sent email has a reply, or nothing has been sent yet.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          <ul aria-label="Waiting on a reply" className="grid content-start gap-2">
            {items.map((i) => (
              <li key={i.outreach_id}>
                <button type="button" onClick={() => setSelected(i.outreach_id)}
                        aria-current={current?.outreach_id === i.outreach_id}
                        className="hover:bg-muted/60 aria-[current=true]:bg-muted grid w-full gap-1 rounded-md border p-3 text-left">
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{i.company_name}</span>
                    <Badge variant={STATUS[i.status]?.variant ?? 'outline'}>{STATUS[i.status]?.label ?? i.status}</Badge>
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {i.founder_name} · sent {daysAgo(i.last_sent_at)} · {i.follow_ups_sent} of 2 follow-ups
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {current && <Detail key={current.outreach_id} item={current} />}
        </div>
      )}
    </div>
  )
}
