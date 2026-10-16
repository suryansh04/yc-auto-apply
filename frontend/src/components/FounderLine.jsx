// A line about the founder personally (operator request 2026-09-25):
// "I found your ML background really interesting, and I felt a genuine
// overlap with the kind of problems I enjoy solving."
//
// Written from their YC bio, or a web search when the bio says too
// little. What it was written from is shown under the options, because
// this is the one line in the letter about a real person, and he should
// be able to check it before it reaches them.
import { useState } from 'react'
import { RefreshCw, Sparkles, UserRound } from 'lucide-react'
import { useUseFounderLine, useWriteFounderLine } from '../api/hooks'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'

const STYLE_LABELS = {
  overlap: 'Overlap with you',
  curious: 'Curious',
  short: 'Short',
}

export default function FounderLine({ outreachId, founderName, currentLine, disabled }) {
  const first = (founderName ?? '').trim().split(/\s+/)[0] || 'the founder'
  const [shown, setShown] = useState([])
  const write = useWriteFounderLine({
    onSuccess: (data) => {
      const lines = (data?.options ?? []).map((o) => o.line)
      if (lines.length) setShown((before) => [...before, ...lines])
    },
  })
  const use = useUseFounderLine()
  const options = write.data?.options ?? []
  const busy = disabled || use.isPending

  function ask() {
    write.mutate({ id: outreachId, avoid: shown })
  }

  return (
    <div className="grid gap-2" aria-label={`Line about ${first}`} role="group">
      <div className="flex items-center gap-2 text-sm font-medium">
        <UserRound className="size-4" aria-hidden />
        A line about {first}
      </div>
      {currentLine ? (
        <div className="bg-muted/50 grid gap-1.5 rounded-md px-3 py-2">
          <p className="text-sm">{currentLine}</p>
          <Button size="sm" variant="ghost" className="w-fit" disabled={busy}
                  onClick={() => use.mutate({ id: outreachId, line: '' })}>
            Remove it
          </Button>
        </div>
      ) : (
        <p className="text-muted-foreground text-xs">The letter has no line about {first} yet.</p>
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" className="w-fit" onClick={ask}
                disabled={write.isPending || busy}>
          {write.isPending ? <Spinner /> : options.length ? <RefreshCw /> : <Sparkles />}
          {options.length ? 'Another' : `Write a line about ${first}`}
        </Button>
        {write.data?.unavailable && !write.isPending && (
          <span className="text-muted-foreground text-xs">{write.data.unavailable}</span>
        )}
        {(write.isError || use.isError) && (
          <span className="text-destructive text-xs">{(write.error ?? use.error).message}</span>
        )}
      </div>
      {options.length > 0 && (
        <ul aria-label={`Suggested lines about ${first}`} className="grid gap-2">
          {options.map((option) => (
            <li key={option.line}
                className="grid gap-1.5 rounded-md border border-amber-400/70 bg-amber-50 p-2 dark:border-amber-500/40 dark:bg-amber-950/40">
              {option.style && (
                <span className="text-muted-foreground text-xs font-medium">
                  {STYLE_LABELS[option.style] ?? option.style}
                </span>
              )}
              <p className="text-sm">{option.line}</p>
              <Button size="sm" className="w-fit" disabled={busy}
                      aria-label={`Use the line: ${option.line}`}
                      onClick={() => use.mutate({ id: outreachId, line: option.line })}>
                {currentLine ? 'Use this instead' : 'Add to letter'}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {write.data?.material && (
        <details className="text-xs">
          <summary className="text-muted-foreground cursor-pointer">
            Written from {write.data.source || 'their YC bio'} - check it before sending
          </summary>
          <p className="mt-1 whitespace-pre-line">{write.data.material}</p>
        </details>
      )}
    </div>
  )
}
