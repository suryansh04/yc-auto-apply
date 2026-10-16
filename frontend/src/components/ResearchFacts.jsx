// What the letter was written from (his pipeline document, 2026-09-25):
// every fact with the source it came from, OLD facts marked, and a note
// on anything the research was unsure about. Links live here, never in
// the email.
import { ExternalLink, Search } from 'lucide-react'
import { Badge } from '@/components/ui/badge'

const KINDS = {
  what_they_do: 'What they do',
  specific_thing: 'Specific thing',
  founder_fact: 'About the founder',
}

export default function ResearchFacts({ research }) {
  const facts = research?.facts ?? []
  return (
    <section aria-label="Research" className="grid gap-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Search className="size-4" aria-hidden />
        Research
      </div>
      {facts.length > 0 ? (
        <ul className="grid gap-2">
          {facts.map((fact) => (
            <li key={`${fact.kind}-${fact.text}`} className="bg-muted/50 grid gap-1 rounded-md px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground text-xs font-medium">
                  {KINDS[fact.kind] ?? fact.kind}
                </span>
                {fact.old && <Badge variant="outline">OLD</Badge>}
              </div>
              <p className="text-sm">{fact.text}</p>
              {fact.url ? (
                <a href={fact.url} target="_blank" rel="noreferrer noopener"
                   className="text-muted-foreground inline-flex w-fit items-center gap-1 text-xs underline-offset-2 hover:underline">
                  {fact.label || 'Source'}
                  <ExternalLink className="size-3" aria-hidden />
                </a>
              ) : (
                <span className="text-muted-foreground text-xs">{fact.label}</span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground text-xs">No verified facts - written from their YC page.</p>
      )}
      {research?.question && (
        <p className="text-xs"><span className="text-muted-foreground">Question it suggested: </span>{research.question}</p>
      )}
      {research?.note && <p className="text-muted-foreground text-xs">{research.note}</p>}
      <p className="text-xs">
        If the email says you read or looked at something, open it before sending.
      </p>
    </section>
  )
}
