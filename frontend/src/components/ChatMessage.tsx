import { AlertCircle, Bot, LoaderCircle, Sparkles, UserRound } from 'lucide-react'
import type { ChatEntry } from '../types/chat'
import { EvidencePanel } from './EvidencePanel'
import { MarkdownContent } from './MarkdownContent'
import { StatusBadge } from './StatusBadge'

const domainLabels = { tax: 'Tax education', investment: 'Investment education', insurance: 'Insurance education' }

interface ChatMessageProps {
  entry: ChatEntry
  onRetry?: () => void
  retryDisabled?: boolean
}

export function ChatMessage({ entry, onRetry, retryDisabled = false }: ChatMessageProps) {
  if (entry.role === 'user') {
    return (
      <article className="message-row user-row">
        <div className="user-message-wrap">
          <div className="message-kicker"><span>{domainLabels[entry.domain]}</span><span>You</span></div>
          <div className="user-message">{entry.content}</div>
        </div>
        <span className="avatar user-avatar" aria-hidden="true"><UserRound size={17} /></span>
      </article>
    )
  }

  const limitations = entry.response?.limitations?.filter((item) => item.trim().length > 0) ?? []
  const freshnessWarnings = limitations.filter((item) =>
    /retrieved on|retrieval date|applicability on|temporal applicability/i.test(item),
  )
  const otherLimitations = limitations.filter((item) => !freshnessWarnings.includes(item))

  return (
    <article className="message-row assistant-row" aria-label="FinPilot answer">
      <span className="avatar assistant-avatar" aria-hidden="true"><Bot size={18} /></span>
      <div className="assistant-message-wrap">
        <div className="message-kicker"><span>FinPilot AI</span>{entry.response && <span>{domainLabels[entry.response.domain]}</span>}</div>
        {entry.pending ? (
          <div className="pending-answer" role="status"><LoaderCircle className="spin" size={17} /> Checking the selected source library…</div>
        ) : entry.error ? (
          <div className="error-panel" role="alert">
            <AlertCircle size={18} /><span>{entry.content}</span>
            {onRetry && <button className="retry-button" type="button" onClick={onRetry} disabled={retryDisabled}>Retry</button>}
          </div>
        ) : entry.response ? (
          <div className="answer-card">
            <div className="answer-topline">
              <StatusBadge tone={entry.response.source_grounding_supported ? 'teal' : 'caution'}>
                {entry.response.source_grounding_supported ? 'Source passages used' : 'Source grounding unavailable'}
              </StatusBadge>
              <StatusBadge tone={entry.response.alignment_status === 'aligned' ? 'positive' : 'neutral'}>
                {entry.response.alignment_status === 'aligned' ? 'Aligned model' : 'Base model'}
              </StatusBadge>
              <span className="alignment-note">Model alignment does not verify answer correctness.</span>
            </div>
            <section className="answer-section" aria-label="Answer">
              <h2 className="section-label">Answer</h2>
              <MarkdownContent className="answer-copy" text={entry.response.answer} />
            </section>
            {entry.response.source_grounding_note && (
              <p className="answer-note"><Sparkles size={14} /> {entry.response.source_grounding_note}</p>
            )}
            {freshnessWarnings.map((warning) => (
              <p className="answer-note freshness-warning" role="status" key={warning}>{warning}</p>
            ))}
            {entry.response.claim_verification_status && (
              <section className="verification-summary" aria-label="Claim verification">
                <h2 className="section-label">Claim verification</h2>
                <StatusBadge tone={
                  entry.response.claim_verification_status === 'fully_verified' ||
                  entry.response.claim_verification_status === 'evidence_supported' ? 'positive' :
                  entry.response.claim_verification_status === 'contains_contradiction' ? 'negative' : 'caution'
                }>
                  {summaryLabel(entry.response.claim_verification_status)}
                </StatusBadge>
                <p className="verification-origin">
                  {entry.response.independent_verification_performed
                    ? 'A separate Nugen verifier assessed cited passages; this is not authoritative review.'
                    : 'No separate verifier ran; only exact sentence matches can be marked supported.'}
                </p>
                {entry.response.claim_verification_note &&
                  (entry.response.independent_verification_performed ||
                   entry.response.claim_verification_status === 'not_performed' ||
                   entry.response.claim_verification_status === 'contains_contradiction' ||
                   /failed|malformed|withheld/i.test(entry.response.claim_verification_note)) &&
                  <p>{entry.response.claim_verification_note}</p>}
              </section>
            )}
            <EvidencePanel
              evidence={entry.response.evidence}
              references={entry.response.source_references}
              claims={entry.response.claim_verifications}
            />
            {otherLimitations.length > 0 && (
              <details className="limitations-details">
                <summary>Limitations <span>{otherLimitations.length}</span></summary>
                <ul>{otherLimitations.map((limitation, index) => (
                  <li key={`${limitation}-${index}`}><MarkdownContent className="markdown-limitation" text={limitation} linkCitations={false} /></li>
                ))}</ul>
              </details>
            )}
            <div className="answer-footer">
              <span>Model <code>{entry.response.model_id}</code></span>
              {entry.response.applicable_as_of && <span>As of {entry.response.applicable_as_of}</span>}
            </div>
          </div>
        ) : null}
      </div>
    </article>
  )
}

function summaryLabel(status: NonNullable<ChatEntry['response']>['claim_verification_status']): string {
  switch (status) {
    case 'fully_verified': return 'Separate verifier: all supported'
    case 'partially_verified': return 'Separate verifier: mixed results'
    case 'evidence_supported': return 'Supported by cited text'
    case 'partially_supported': return 'Some claims match cited text'
    case 'verification_unavailable': return 'Verification unavailable'
    case 'contains_contradiction': return 'Contradiction detected'
    case 'not_performed': return 'Not performed'
    case 'unverified': return 'Unverified'
    default: return 'Verification unavailable'
  }
}
