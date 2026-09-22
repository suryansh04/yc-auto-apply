// The PDF attached to every letter (operator request, 2026-09-22).
//
// It started as a card on the Approved screen and moved here: it is not
// about any one letter, it is the one file every founder receives, and it
// has enough to say about itself - what is attached, when it changed, and
// what the drafter was told it contains - to be worth a page.
//
// The file is shown, not linked. "View the resume" means looking at it,
// and a link that opens another tab is one more click before the thing
// the page exists for.
import { useRef } from 'react'
import { ExternalLink, Upload } from 'lucide-react'
import { useReplaceResume, useResumeStatus } from '../api/hooks'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Item, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'

function kilobytes(bytes) {
  return `${Math.round((bytes ?? 0) / 1024)} KB`
}

function when(iso) {
  if (!iso) return 'unknown'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? 'unknown' : date.toLocaleString()
}

export default function Resume() {
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

  if (status.isLoading) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-[32rem] w-full" />
      </div>
    )
  }

  if (status.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not read the resume</AlertTitle>
        <AlertDescription>{status.error.message}</AlertDescription>
      </Alert>
    )
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My resume</h1>
          <p className="text-muted-foreground text-sm">
            The file attached to every letter you send. Replacing it here changes what every
            founder receives from the next send onwards.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {data?.configured && (
            <Button asChild variant="outline">
              <a href="/api/resume" target="_blank" rel="noreferrer">
                Open in a tab <ExternalLink />
              </a>
            </Button>
          )}
          <Button disabled={replace.isPending} onClick={() => fileInput.current?.click()}>
            {replace.isPending ? <Spinner /> : <Upload />}
            {data?.configured ? 'Replace' : 'Upload a resume'}
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf,.pdf"
            aria-label="Replace the resume"
            // sr-only rather than hidden: display:none takes it out of
            // the accessibility tree and out of reach of the keyboard,
            // leaving the button as the only way in.
            className="sr-only"
            onChange={choose}
          />
        </div>
      </div>

      {replace.isError && (
        <Alert variant="destructive" role="alert">
          <AlertTitle>Could not replace the resume</AlertTitle>
          <AlertDescription>{replace.error.message}</AlertDescription>
        </Alert>
      )}
      {replace.isSuccess && (
        <Alert>
          <AlertTitle>Resume replaced</AlertTitle>
          <AlertDescription>
            {/* The consequence nobody would guess: the claims the drafter
                is held to come from `parse-resume`, not from this file.
                Letting the two drift apart silently would be worse than
                either. */}
            Every letter from now on carries the new file. The {data?.claims ?? 0} claims the
            drafter is held to still come from the old one &mdash; run{' '}
            <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">
              python -m app.cli parse-resume
            </code>{' '}
            to re-read it.
          </AlertDescription>
        </Alert>
      )}

      {!data?.configured ? (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>No resume yet</EmptyTitle>
            <EmptyDescription>
              Nothing can be sent without one &mdash; every letter says the resume is attached.
              Upload a PDF to fix that.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
          <Card className="overflow-hidden py-0">
            <CardContent className="px-0">
              {/* The file itself, and the only way to be sure what a
                  founder opens is to look at what they open. `key` on the
                  timestamp so a replacement re-fetches instead of showing
                  the browser's cached copy of the old PDF. */}
              <iframe
                key={data.modified_at}
                src="/api/resume"
                title="My resume"
                className="h-[42rem] w-full border-0"
              />
            </CardContent>
          </Card>

          <aside className="grid gap-4 xl:sticky xl:top-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">The file</CardTitle>
                <CardDescription>What `send` attaches.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2">
                <Item variant="muted">
                  <ItemContent>
                    <ItemDescription>Filename</ItemDescription>
                    <ItemTitle className="font-mono text-xs">{data.filename}</ItemTitle>
                  </ItemContent>
                </Item>
                <Item variant="muted">
                  <ItemContent>
                    <ItemDescription>Size</ItemDescription>
                    <ItemTitle>{kilobytes(data.size_bytes)}</ItemTitle>
                  </ItemContent>
                </Item>
                <Item variant="muted">
                  <ItemContent>
                    <ItemDescription>Last changed</ItemDescription>
                    <ItemTitle>{when(data.modified_at)}</ItemTitle>
                  </ItemContent>
                </Item>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">What the drafter knows</CardTitle>
                <CardDescription>
                  {data.claims} claims parsed from a resume. These are the only things a draft
                  may say about you &mdash; and they come from{' '}
                  <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">
                    parse-resume
                  </code>
                  , not from this file. Replacing the PDF does not re-read it.
                </CardDescription>
              </CardHeader>
            </Card>
          </aside>
        </div>
      )}
    </>
  )
}
