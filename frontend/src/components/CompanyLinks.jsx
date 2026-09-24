// The company's own site and its YC page, one click from the letter.
//
// Operator request (2026-09-25): "give the company official website link
// to both their product website and also yc website". Both open in a new
// tab: the letter being reviewed must still be here when he comes back.
import { ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'

function hostOf(url) {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

export default function CompanyLinks({ website, ycUrl, className }) {
  if (!website && !ycUrl) return null
  return (
    <div className={className ?? 'flex flex-wrap gap-2'}>
      {website && (
        <Button asChild size="sm" variant="outline">
          <a href={website} target="_blank" rel="noreferrer noopener"
             aria-label={`Company website, ${hostOf(website)}`}>
            {hostOf(website)} <ExternalLink />
          </a>
        </Button>
      )}
      {ycUrl && (
        <Button asChild size="sm" variant="outline">
          <a href={ycUrl} target="_blank" rel="noreferrer noopener" aria-label="Y Combinator page">
            YC page <ExternalLink />
          </a>
        </Button>
      )}
    </div>
  )
}
