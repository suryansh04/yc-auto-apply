// Task 17: the Thread screen - the operator's whole-conversation read
// view for one outreach (backend/app/api/routers/thread.py's
// `GET /api/outreach/{id}/thread`). Every outbound message and every
// reply, already in time order from the backend, a reply's label with
// its confidence/rationale, a one-click correction control, and a link
// out to the operator's own Gmail thread.
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { useSetLabel, useThread } from '../api/hooks'
import { outreachStateLabel } from '../labels'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'

// app/domain/replies.py's LABELS/MACHINE_LABELS, mirrored by hand (no
// generated types) - the six labels an operator can correct a reply to,
// and which two of them are the automatic classifier's own signals (an
// autoresponder, an out-of-office bounce) rather than a founder's own
// words. An operator glancing at the timeline must never mistake one for
// the other, so these get a distinct badge, not just distinct text.
const LABELS = ['INTERESTED', 'REJECTED', 'REFERRAL', 'AUTO_REPLY', 'OUT_OF_OFFICE', 'UNCLEAR']
const MACHINE_LABELS = new Set(['AUTO_REPLY', 'OUT_OF_OFFICE'])

// The value sent to the backend is the label; the text on screen is what
// the reply actually was. Shouting INTERESTED at the operator tells them
// nothing the sentence case doesn't.
const LABEL_TEXT = {
  INTERESTED: 'Interested',
  REJECTED: 'Not interested',
  REFERRAL: 'Referred me on',
  AUTO_REPLY: 'Automatic reply',
  OUT_OF_OFFICE: 'Out of office',
  UNCLEAR: 'Unclear',
}

function LabelBadge({ label, confidence, rationale, corrected }) {
  if (!label) return <Badge variant="outline">Not sorted yet</Badge>
  const machine = MACHINE_LABELS.has(label)
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={machine ? 'outline' : 'default'}>{LABEL_TEXT[label] ?? label}</Badge>
      {machine && <Badge variant="secondary">automated</Badge>}
      {confidence != null && (
        <span className="text-muted-foreground text-xs">
          confidence {Math.round(confidence * 100)}%
        </span>
      )}
      {rationale && <span className="text-muted-foreground text-xs">{rationale}</span>}
      {corrected && <Badge variant="secondary">you changed this</Badge>}
    </div>
  )
}

// The one-click correction control: one select per reply (still one
// interaction, one control) rather than six always-visible buttons -
// twelve buttons on a two-reply thread was the problem this replaced.
// Picking an option fires the correction immediately; the select stays
// on its placeholder afterwards (it's a one-shot action menu, not a
// field showing "the current label" - that's LabelBadge's job, above).
function LabelCorrection({ message, threadId, setLabel }) {
  const [error, setError] = useState(null)

  function correct(label) {
    if (!label) return
    setError(null)
    setLabel.mutate(
      { messageId: message.id, label, threadId },
      { onError: (err) => setError(err.message) },
    )
  }

  return (
    <div className="grid gap-1">
      <Select value="" onValueChange={correct} disabled={setLabel.isPending}>
        <SelectTrigger
          className="w-56"
          aria-label={`Correct label for message ${message.id}`}
        >
          <SelectValue placeholder="Change label…" />
        </SelectTrigger>
        <SelectContent>
          {LABELS.map((label) => (
            <SelectItem key={label} value={label}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && (
        <p role="alert" className="text-destructive text-xs">
          {error}
        </p>
      )}
    </div>
  )
}

function MessageCard({ message, threadId, setLabel }) {
  const timestamp = message.sent_at ?? message.received_at
  // V3 (T18 fix wave 2): an outbound row with no `sent_at` is still
  // pending_approval/approved - nothing was actually sent yet, so
  // labelling it "Sent" is a lie the operator would act on.
  const direction =
    message.direction === 'outbound' ? (message.sent_at ? 'Sent' : 'Draft') : 'Reply'

  return (
    <Card className={message.direction === 'inbound' ? 'bg-muted/40' : undefined}>
      <CardHeader>
        <CardDescription className="flex items-center gap-2">
          <Badge variant={message.direction === 'inbound' ? 'secondary' : 'outline'}>
            {direction}
          </Badge>
          {timestamp && (
            <time dateTime={timestamp}>{new Date(timestamp).toLocaleString()}</time>
          )}
        </CardDescription>
        {message.subject && <CardTitle className="text-base">{message.subject}</CardTitle>}
      </CardHeader>
      <CardContent>
        <p className="text-sm whitespace-pre-wrap">{message.body}</p>
      </CardContent>
      {message.direction === 'inbound' && (
        <CardFooter className="flex-col items-start gap-2">
          <LabelBadge
            label={message.label}
            confidence={message.confidence}
            rationale={message.rationale}
            corrected={message.corrected}
          />
          <LabelCorrection message={message} threadId={threadId} setLabel={setLabel} />
        </CardFooter>
      )}
    </Card>
  )
}

export default function Thread() {
  const { id } = useParams()
  const outreachId = Number(id)
  const threadQuery = useThread(outreachId)
  const setLabel = useSetLabel()

  if (threadQuery.isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  if (threadQuery.isError) {
    if (threadQuery.error?.status === 404) {
      return (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>That conversation does not exist.</EmptyTitle>
            <EmptyDescription>
              <Link className="underline underline-offset-4" to="/contacts">
                Back to founders
              </Link>
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )
    }
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load thread</AlertTitle>
        <AlertDescription>{threadQuery.error.message}</AlertDescription>
      </Alert>
    )
  }

  const thread = threadQuery.data
  if (!thread) return null

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {thread.founder_name ?? 'Founder'} at {thread.company_name ?? 'Unknown company'}
        </h1>
        <p className="text-muted-foreground text-sm">
          {/* The same words as everywhere else: this said
              "needs_manual_email" while the Pipeline and Founders both
              said "Needs an address" for the same row. */}
          {thread.to ?? 'no address yet'} &mdash; {outreachStateLabel(thread.state)}
        </p>
        {thread.gmail_url && (
          <Button asChild variant="outline" size="sm" className="mt-3">
            <a href={thread.gmail_url} target="_blank" rel="noreferrer">
              Open in Gmail <ExternalLink />
            </a>
          </Button>
        )}
      </div>

      {thread.messages.length === 0 ? (
        // Reachable: a founder with no address has nothing drafted yet,
        // so the timeline is empty. A blank page under a heading reads
        // as a failure rather than as "nothing has happened".
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Nothing has been sent yet</EmptyTitle>
            <EmptyDescription>
              Messages appear here once a letter is drafted and sent.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ol className="grid max-w-3xl gap-4">
          {thread.messages.map((message) => (
            <li key={message.id}>
              <MessageCard message={message} threadId={outreachId} setLabel={setLabel} />
            </li>
          ))}
        </ol>
      )}
    </>
  )
}
