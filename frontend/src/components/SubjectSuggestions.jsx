// Subject lines written for this company (operator request 2026-09-23):
// "a agent specifically for writing random catchy punchy subject instead
// of generic one".
//
// Asked for on demand, like the company paragraph's AI tab: it costs a
// model call, and new drafts already arrive with a written subject. This
// is for the letters drafted before that, and for "give me another".
// Picking one saves it through the same edit every other change uses, so
// the checks re-run over it.
import { useState } from 'react'
import { RefreshCw, Sparkles } from 'lucide-react'
import { useSuggestSubjects } from '../api/hooks'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'

export default function SubjectSuggestions({ outreachId, onUse, disabled }) {
  const [shown, setShown] = useState([])
  const suggest = useSuggestSubjects({
    onSuccess: (data) => {
      if (data?.subjects?.length) setShown((before) => [...before, ...data.subjects])
    },
  })
  const latest = suggest.data?.subjects ?? []

  function ask() {
    // Everything already on screen is named, so "More" asks a different
    // question from the first press rather than the same one again.
    suggest.mutate({ id: outreachId, avoid: shown })
  }

  return (
    <div className="grid gap-2 pt-1">
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" className="w-fit" onClick={ask}
                disabled={suggest.isPending || disabled}>
          {suggest.isPending ? <Spinner /> : latest.length ? <RefreshCw /> : <Sparkles />}
          {latest.length ? 'More subjects' : 'Suggest subjects'}
        </Button>
        {suggest.data?.unavailable && !suggest.isPending && (
          <span className="text-muted-foreground text-xs">{suggest.data.unavailable}</span>
        )}
        {suggest.isError && (
          <span className="text-destructive text-xs">{suggest.error.message}</span>
        )}
      </div>
      {latest.length > 0 && (
        <ul aria-label="Suggested subjects" className="grid gap-1">
          {latest.map((subject) => (
            <li key={subject}
                className="bg-muted/50 flex items-center justify-between gap-3 rounded-md px-3 py-1.5">
              <span className="text-sm">{subject}</span>
              <Button size="sm" variant="ghost" disabled={disabled}
                      aria-label={`Use subject: ${subject}`} onClick={() => onUse(subject)}>
                Use
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
