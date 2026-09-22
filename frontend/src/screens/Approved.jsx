// Operator request (2026-09-22): a screen of its own for the letters
// that are approved and waiting to go.
//
// They were invisible. The review queue holds only undecided drafts, and
// once a letter is approved it fell out of every screen that shows whole
// letters - yet those are precisely the ones about to reach a founder.
// This is the last place anything can be caught, so it shows the composed
// wire text (ruling R2: byte-for-byte what is sent), not a summary row.
//
// Sending is deliberately one button for the whole batch, not one per
// letter: `POST /api/send` sends what is approved, up to the daily cap,
// and inventing a per-letter send here would mean a second way to send
// that the backend does not actually have.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, Send } from 'lucide-react'
import { useApproved, useSend } from '../api/hooks'
import { describeSendResult } from './Pipeline'
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
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'

// app/domain/letter.py's USER_FIT_MARKER. A letter still carrying it
// cannot be sent (`send_approved` refuses), so saying so here is the
// difference between "nothing happened" and "this one needs a minute".
const USER_FIT_MARKER = '>>> YOUR TURN'

function ApprovedLetter({ item, defaultOpen }) {
  const needsYourParagraph = (item.wire_text ?? '').includes(USER_FIT_MARKER)
  return (
    <Card>
      <Collapsible defaultOpen={defaultOpen}>
        <CardHeader>
          <CardDescription>
            {item.company_name ?? 'Unknown company'} &middot; {item.to ?? 'no address'}
          </CardDescription>
          <CardTitle className="text-base">{item.subject ?? '(no subject)'}</CardTitle>
          <CardAction>
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm">
                Read <ChevronDown />
              </Button>
            </CollapsibleTrigger>
          </CardAction>
        </CardHeader>
        {needsYourParagraph && (
          <CardContent>
            <Alert>
              <AlertTitle>This one will not send yet</AlertTitle>
              <AlertDescription>
                It still has the{' '}
                <span className="font-mono">{USER_FIT_MARKER}</span> paragraph. Send it back with{' '}
                <Link className="underline underline-offset-4" to="/queue">
                  Review
                </Link>{' '}
                to fill it in or delete it.
              </AlertDescription>
            </Alert>
          </CardContent>
        )}
        <CollapsibleContent>
          <Separator />
          <CardContent className="pt-6">
            <pre className="font-sans text-[15px] leading-relaxed whitespace-pre-wrap">
              {item.wire_text}
            </pre>
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  )
}

export default function Approved() {
  const approvedQuery = useApproved()
  const send = useSend()
  const [sentResult, setSentResult] = useState(null)

  const items = approvedQuery.data ?? []
  const blocked = items.filter((it) => (it.wire_text ?? '').includes(USER_FIT_MARKER)).length
  const ready = items.length - blocked

  function handleSend() {
    // The one irreversible thing this app does. It says how many are
    // about to leave, and never counts the ones that cannot.
    const ok = window.confirm(
      `Send ${ready} ${ready === 1 ? 'letter' : 'letters'} now? This cannot be undone.`,
    )
    if (!ok) return
    send.mutate(undefined, { onSuccess: (data) => setSentResult(data) })
  }

  if (approvedQuery.isLoading) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  if (approvedQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load the approved letters</AlertTitle>
        <AlertDescription>{approvedQuery.error.message}</AlertDescription>
      </Alert>
    )
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Approved</h1>
          <p className="text-muted-foreground text-sm">
            Letters you have approved, waiting to be sent. Read one more time if you like &mdash;
            after this they are in someone&rsquo;s inbox.
          </p>
        </div>
        {items.length > 0 && (
          <Button disabled={ready === 0 || send.isPending} onClick={handleSend}>
            {send.isPending ? <Spinner /> : <Send />}
            Send {ready} now
          </Button>
        )}
      </div>

      {sentResult && (
        <Alert>
          <AlertTitle>
            {sentResult.sent === 1 ? '1 letter sent' : `${sentResult.sent} letters sent`}
          </AlertTitle>
          <AlertDescription>{describeSendResult(sentResult)}</AlertDescription>
        </Alert>
      )}
      {send.isError && (
        <Alert variant="destructive">
          <AlertTitle>Could not send</AlertTitle>
          <AlertDescription>{send.error.message}</AlertDescription>
        </Alert>
      )}

      {blocked > 0 && (
        <Badge variant="outline" className="w-fit">
          {blocked} {blocked === 1 ? 'letter needs' : 'letters need'} your paragraph before sending
        </Badge>
      )}

      {items.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Nothing approved yet</EmptyTitle>
            <EmptyDescription>
              Letters you approve in{' '}
              <Link className="underline underline-offset-4" to="/queue">
                Review
              </Link>{' '}
              wait here until you send them.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="grid max-w-3xl gap-3">
          {items.map((item, index) => (
            <ApprovedLetter
              key={item.outreach_id}
              item={item}
              defaultOpen={items.length === 1 && index === 0}
            />
          ))}
        </div>
      )}
    </>
  )
}
