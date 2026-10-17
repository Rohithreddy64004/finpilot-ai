import { ArrowUpRight, BookOpen, ChevronDown } from 'lucide-react'
import type { ClaimVerification, RetrievedEvidence, SourceReference } from '../types/api'
import { MarkdownContent } from './MarkdownContent'
import { StatusBadge, type BadgeTone } from './StatusBadge'

interface EvidencePanelProps {
  evidence?: RetrievedEvidence[]
  references?: SourceReference[]
  claims?: ClaimVerification[]
}

function claimTone(status: ClaimVerification['status']): BadgeTone {
  if (status === 'supported') return 'positive'
  if (status === 'contradicted') return 'negative'
  return 'caution'
}

function claimLabel(status: ClaimVerification['status']): string {
  if (status === 'supported') return 'Supported'
  if (status === 'contradicted') return 'Contradicted'
  return 'Unverified'
}

function claimBasisLabel(claim: ClaimVerification): string {
  if (claim.assessment_basis === 'independent_verifier') return 'Separate verifier assessment'
  if (claim.assessment_basis === 'source_text_comparison' && claim.status === 'supported') {
    return 'Direct wording match in cited text'
  }
  if (claim.assessment_basis === 'source_text_comparison') return 'No direct wording match'
  return 'No assessment was available'
}

function usefulClaimNote(note: string): boolean {
  return Boolean(note) && ![
    'Claim sentence exactly matches a sentence in its cited passage.',
    "No direct wording match was found in this claim's cited passage(s).",
    'No valid evidence citation appears on this claim.',
  ].includes(note) &&
    !note.startsWith('A configured verifier assessed this claim as ') &&
    !note.startsWith('The verifier could not establish whether ') &&
    !note.startsWith('Claim verification failed or returned invalid evidence;')
}

function SafeSourceLink({ source }: { source: SourceReference }) {
  const url = source.url
  if (!url) return null
  let safeUrl: URL
  try {
    safeUrl = new URL(url)
  } catch {
    return null
  }
  if ((safeUrl.protocol !== 'https:' && safeUrl.protocol !== 'http:') || safeUrl.username || safeUrl.password) {
    return null
  }
  return (
    <a className="source-link" href={safeUrl.href} target="_blank" rel="noopener noreferrer">
      Open source <ArrowUpRight size={13} aria-hidden="true" />
    </a>
  )
}

export function EvidencePanel({
  evidence = [],
  references = [],
  claims = [],
}: EvidencePanelProps) {
  const hasContent = evidence.length > 0 || references.length > 0 || claims.length > 0
  if (!hasContent) return null

  return (
    <div className="answer-details">
      {claims.length > 0 && (
        <section className="claim-section" aria-label="Claim assessments">
          <details className="claim-details" open={claims.length <= 2}>
            <summary>
              <span><BookOpen size={15} /> Per-claim results ({claims.length})</span>
              <ChevronDown className="details-chevron" size={16} />
            </summary>
            <ul className="claim-list">
              {claims.map((claim, index) => (
                <li className="claim-item" key={`${claim.claim_text}-${index}`}>
                  <div className="claim-heading">
                    <StatusBadge tone={claimTone(claim.status)}>{claimLabel(claim.status)}</StatusBadge>
                    <span className="claim-basis">{claimBasisLabel(claim)}</span>
                  </div>
                  <MarkdownContent className="claim-copy" text={claim.claim_text} linkCitations={false} />
                  {usefulClaimNote(claim.note) && <MarkdownContent className="claim-note" text={claim.note} linkCitations={false} />}
                  {claim.evidence_ids.length > 0 && (
                    <div className="claim-evidence-links" aria-label="Supporting evidence">
                      {claim.evidence_ids.map((id) => (
                        <a key={id} href={`#evidence-${id}`} className="markdown-citation">[{id}]</a>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}

      {(evidence.length > 0 || references.length > 0) && (
        <details className="evidence-details">
          <summary>
            <span><BookOpen size={15} /> Sources &amp; evidence ({evidence.length || references.length})</span>
            <ChevronDown className="details-chevron" size={16} />
          </summary>
          {evidence.length > 0 && (
            <div className="evidence-list">
              {evidence.map((item) => (
                <article className="evidence-item" id={`evidence-${item.citation_id}`} key={item.passage_id}>
                  <div className="evidence-meta">
                    <StatusBadge tone="teal">[{item.citation_id}]</StatusBadge>
                    <span>{item.document_name}</span>
                  </div>
                  <MarkdownContent className="evidence-excerpt" text={item.excerpt} linkCitations={false} />
                  <div className="source-meta">
                    <span>{item.source.title}{item.source.publisher ? ` · ${item.source.publisher}` : ''}</span>
                    {item.source.accessed_on && <span>Retrieved {item.source.accessed_on}</span>}
                    <SafeSourceLink source={item.source} />
                  </div>
                </article>
              ))}
            </div>
          )}
          {evidence.length === 0 && references.length > 0 && (
            <div className="reference-list">
              {references.map((source, index) => (
                <div className="reference-row" key={`${source.url || source.title}-${index}`}>
                  <div>
                    <strong>{source.title}</strong>
                    <span>{source.publisher || 'Source'}{source.accessed_on ? ` · Retrieved ${source.accessed_on}` : ''}</span>
                  </div>
                  <SafeSourceLink source={source} />
                </div>
              ))}
            </div>
          )}
        </details>
      )}
    </div>
  )
}
