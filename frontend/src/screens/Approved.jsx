// Operator request (2026-09-22): a screen of its own for the letters
// that are approved and waiting to go.
//
// They were invisible. The review queue holds only undecided drafts, and
// once a letter is approved it fell out of every screen that shows whole
// letters - yet those are precisely the ones about to reach a founder.
// This is the last place anything can be caught, so it shows the composed
// wire text (ruling R2: byte-for-byte what is sent), not a summary row.
//
// Each letter can be edited here and sent on its own. Waiting to send is
// exactly when a typo gets noticed, and bouncing the letter back through
// the review queue to fix one word is busywork; an edit re-stamps the
// approval onto the new text, because the old one approved words that no
// longer exist.
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, FileText, Pencil, Send, Undo2, Upload } from 'lucide-react'
import {
  useApproved,
  useEdit,
  useReplaceResume,
  useResumeStatus,
  useSend,
  useUnapprove,
} from '../api/hooks'
import { describeSendResult } from './Pipeline'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'

// app/domain/letter.py's USER_FIT_MARKER. A letter still carrying it
// cannot be sent (`send_approved` refuses), so saying so here is the
// difference between "nothing happened" and "this one needs a minute".
const USER_FIT_MARKER = '>>> YOUR TURN'

function blocked(item) {
  return (item.wire_text ?? '').includes(USER_FIT_MARKER)
}

// The editor, seeded once from the stored draft. `errors` changing never
// resets it: a 422 from the validator must not wipe out what was typed.
function EditLetter({ item, saving, errors, onSave, onCancel }) {
  const [subject, setSubject] = useState(item.subject ?? '')
  const [body, setBody] = useState(item.body ?? '')

  return (
    <>
      <Separator />
      <CardContent className="grid gap-4 pt-6">
        {errors.length > 0 && (
          <Alert variant="destructive" role="alert">
            <AlertTitle>This edit was not saved</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-4">
                {errors.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
        <div className="grid gap-2">
          <Label htmlFor={`subject-${item.outreach_id}`}>Subject</Label>
          <Input
            id={`subject-${item.outreach_id}`}
            aria-label="Subject"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`body-${item.outreach_id}`}>Body</Label>
          {/* The body, not the wire text: the greeting, signature and
              opt-out line are added at send time and are not editable
              here - editing them would change every letter. */}
          <Textarea
            id={`body-${item.outreach_id}`}
            aria-label="Body"
            rows={16}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        </div>
      </CardContent>
      <CardFooter className="gap-2">
        <Button disabled={saving} onClick={() => onSave(subject, body)}>
          {saving ? <Spinner /> : null}
          Save
        </Button>
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </CardFooter>
    </>
  )
}

// The PDF attached to every letter. It is the one part of an outgoing
// email nothing in the UI could show - the letter was reviewable down to
// the byte while the attachment beside it was a filename in a footer.
//
// One card, not a control per letter: there is one file and it goes to
// everybody.
function ResumeCard() {
  const status = useResumeStatus()
  const replace = useReplaceResume()
  const fileInput = useRef(null)
  const data = status.data

  function choose(event) {
    const file = event.target.files?.[0]
    // Let the same file be picked twice in a row (after a failure, say).
    event.target.value = ''
    if (file) replace.mutate(file)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <FileText className="size-4" />
          Resume
        </CardTitle>
        <CardDescription>
          {data?.configured
            ? `${data.filename} · ${Math.round((data.size_bytes ?? 0) / 1024)} KB · attached to every letter below`
            : 'No resume file is configured, so nothing can be sent.'}
        </CardDescription>
        <CardAction className="flex items-center gap-1">
          {data?.configured && (
            <Button asChild variant="ghost" size="sm">
              {/* A new tab, not a download: the point is to look at it. */}
              <a href="/api/resume" target="_blank" rel="noreferrer">
                View
              </a>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={replace.isPending}
            onClick={() => fileInput.current?.click()}
          >
            {replace.isPending ? <Spinner /> : <Upload />}
            Replace
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf,.pdf"
            aria-label="Replace the resume"
            // sr-only rather than hidden: display:none takes it out of
            // the accessibility tree and out of reach of the keyboard,
            // leaving the Replace button as the only way in.
            className="sr-only"
            onChange={choose}
          />
        </CardAction>
      </CardHeader>
      {(replace.isError || replace.isSuccess) && (
        <CardContent>
          {replace.isError ? (
            <Alert variant="destructive" role="alert">
              <AlertTitle>Could not replace the resume</AlertTitle>
              <AlertDescription>{replace.error.message}</AlertDescription>
            </Alert>
          ) : (
            <Alert>
              <AlertTitle>Resume replaced</AlertTitle>
              <AlertDescription>
                {/* The consequence nobody would guess: the claims the
                    drafter is held to come from `parse-resume`, not from
                    this file. Letting them drift apart silently would be
                    worse than either. */}
                Every letter from now on carries the new file. The {data?.claims ?? 0} claims the
                drafter is held to still come from the old one &mdash; run{' '}
                <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">
                  python -m app.cli parse-resume
                </code>{' '}
                to re-read it.
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function ApprovedLetter({ item, defaultOpen }) {
  const [isEditing, setIsEditing] = useState(false)
  const [editErrors, setEditErrors] = useState([])
  const [sent, setSent] = useState(null)
  const edit = useEdit()
  const send = useSend()
  const unapprove = useUnapprove()
  const needsYourParagraph = blocked(item)

  function save(subject, body) {
    edit.mutate(
      { id: item.outreach_id, subject, body },
      {
        onSuccess: () => {
          setIsEditing(false)
          setEditErrors([])
        },
        // 422 carries the validator's own messages, verbatim. Nothing was
        // persisted and the editor keeps what was typed.
        onError: (err) => setEditErrors(err.errors ?? [err.message]),
      },
    )
  }

  function sendThisOne() {
    const ok = window.confirm(
      `Send this letter to ${item.to}? This cannot be undone.`,
    )
    if (!ok) return
    send.mutate(
      { outreachId: item.outreach_id },
      { onSuccess: (data) => setSent(data) },
    )
  }

  function backToReview() {
    // No confirmation: nothing is lost. The text is untouched and the
    // letter reappears in Review, where approving it again is one key.
    unapprove.mutate(item.outreach_id)
  }

  return (
    <Card>
      <Collapsible defaultOpen={defaultOpen} open={isEditing ? true : undefined}>
        <CardHeader>
          <CardDescription>
            {item.company_name ?? 'Unknown company'} &middot; {item.to ?? 'no address'}
          </CardDescription>
          <CardTitle className="text-base">{item.subject ?? '(no subject)'}</CardTitle>
          <CardAction className="flex items-center gap-1">
            {!isEditing && (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setEditErrors([])
                    setIsEditing(true)
                  }}
                >
                  <Pencil /> Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={unapprove.isPending || Boolean(sent)}
                  onClick={backToReview}
                >
                  {unapprove.isPending ? <Spinner /> : <Undo2 />}
                  Back to review
                </Button>
                <Button
                  size="sm"
                  disabled={needsYourParagraph || send.isPending || Boolean(sent)}
                  onClick={sendThisOne}
                >
                  {send.isPending ? <Spinner /> : <Send />}
                  Send
                </Button>
                <CollapsibleTrigger asChild>
                  <Button variant="ghost" size="sm">
                    Read <ChevronDown />
                  </Button>
                </CollapsibleTrigger>
              </>
            )}
          </CardAction>
        </CardHeader>

        {sent && (
          <CardContent>
            <Alert>
              <AlertTitle>{sent.sent === 1 ? 'Sent' : 'Not sent'}</AlertTitle>
              <AlertDescription>
                {sent.sent === 1 ? 'It is in their inbox.' : describeSendResult(sent)}
              </AlertDescription>
            </Alert>
          </CardContent>
        )}
        {send.isError && (
          <CardContent>
            <Alert variant="destructive" role="alert">
              <AlertTitle>Could not send</AlertTitle>
              <AlertDescription>{send.error.message}</AlertDescription>
            </Alert>
          </CardContent>
        )}
        {unapprove.isError && (
          <CardContent>
            <Alert variant="destructive" role="alert">
              <AlertTitle>Could not send it back</AlertTitle>
              <AlertDescription>{unapprove.error.message}</AlertDescription>
            </Alert>
          </CardContent>
        )}

        {needsYourParagraph && !isEditing && (
          <CardContent>
            <Alert>
              <AlertTitle>This one will not send yet</AlertTitle>
              <AlertDescription>
                It still has the <span className="font-mono">{USER_FIT_MARKER}</span> paragraph.
                Press Edit to write it, or delete that paragraph.
              </AlertDescription>
            </Alert>
          </CardContent>
        )}

        {isEditing ? (
          <EditLetter
            item={item}
            saving={edit.isPending}
            errors={editErrors}
            onSave={save}
            onCancel={() => {
              setIsEditing(false)
              setEditErrors([])
            }}
          />
        ) : (
          <CollapsibleContent>
            <Separator />
            <CardContent className="pt-6">
              <pre className="font-sans text-[15px] leading-relaxed whitespace-pre-wrap">
                {item.wire_text}
              </pre>
            </CardContent>
          </CollapsibleContent>
        )}
      </Collapsible>
    </Card>
  )
}

export default function Approved() {
  const approvedQuery = useApproved()
  const sendAll = useSend()
  const [sentResult, setSentResult] = useState(null)

  const items = approvedQuery.data ?? []
  const blockedCount = items.filter(blocked).length
  const ready = items.length - blockedCount

  function handleSendAll() {
    // The one irreversible thing this app does. It says how many are
    // about to leave, and never counts the ones that cannot.
    const ok = window.confirm(
      `Send ${ready} ${ready === 1 ? 'letter' : 'letters'} now? This cannot be undone.`,
    )
    if (!ok) return
    sendAll.mutate(undefined, { onSuccess: (data) => setSentResult(data) })
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
            Letters you have approved, waiting to be sent. Edit one, send one, send them back
            to Review, or send the lot &mdash; after that they are in someone&rsquo;s inbox.
          </p>
        </div>
        {items.length > 0 && (
          <Button disabled={ready === 0 || sendAll.isPending} onClick={handleSendAll}>
            {sendAll.isPending ? <Spinner /> : <Send />}
            Send all {ready}
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
      {sendAll.isError && (
        <Alert variant="destructive">
          <AlertTitle>Could not send</AlertTitle>
          <AlertDescription>{sendAll.error.message}</AlertDescription>
        </Alert>
      )}

      <div className="max-w-3xl">
        <ResumeCard />
      </div>

      {blockedCount > 0 && (
        <Badge variant="outline" className="w-fit">
          {blockedCount} {blockedCount === 1 ? 'letter needs' : 'letters need'} your paragraph
          before sending
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
