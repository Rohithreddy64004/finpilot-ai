import { useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import type { AskSource } from '../types/api'

function safeSourceUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    const hostname = url.hostname.toLowerCase()
    if ((url.protocol !== 'https:' && url.protocol !== 'http:') || url.username || url.password ||
        hostname === 'localhost' || hostname.endsWith('.localhost') || hostname === '127.0.0.1' || hostname === '[::1]') return null
    return url.href
  } catch {
    return null
  }
}

export function SourceList({ sources }: { sources: AskSource[] }) {
  const [showAll, setShowAll] = useState(false)
  if (!sources.length) return null
  const visibleSources = showAll ? sources : sources.slice(0, 3)

  return (
    <section className="sources-section" aria-labelledby="sources-heading">
      <h2 id="sources-heading">Sources</h2>
      <ol className="source-list">
        {visibleSources.map((item) => {
          const sourceUrl = safeSourceUrl(item.source.url)
          return (
            <li className="source-card" id={`evidence-${item.citation_id}`} key={item.citation_id}>
              <div className="source-card-heading">
                <span className="citation-label">[{item.citation_id}]</span>
                <h3>{item.source.title}</h3>
              </div>
              {item.source.publisher && <p className="source-publisher">{item.source.publisher}</p>}
              {item.section_heading && <p className="source-section">{item.section_heading}</p>}
              <p className="source-snippet">{item.excerpt}</p>
              <div className="source-card-footer">
                <span>{item.source.accessed_on ? `Retrieved ${item.source.accessed_on}` : 'Retrieved date unavailable'}</span>
                {sourceUrl && (
                  <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
                    Open source <ArrowUpRight size={13} aria-hidden="true" />
                  </a>
                )}
              </div>
            </li>
          )
        })}
      </ol>
      {sources.length > 3 && (
        <button className="show-sources-button" type="button" onClick={() => setShowAll((value) => !value)}>
          {showAll ? 'Show fewer' : `Show all (${sources.length})`}
        </button>
      )}
    </section>
  )
}
