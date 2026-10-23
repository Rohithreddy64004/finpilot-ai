import { useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { AskExpertResult, AskResponse, ClaimVerification, Domain } from '../types/api'
import { MarkdownContent } from './MarkdownContent'
import { SourceList } from './SourceList'
import { StatusBadge } from './StatusBadge'

const expertNames: Record<Domain, string> = {
  tax: 'Tax', investment: 'Investment', insurance: 'Insurance',
}

interface AskResponsePanelProps {
  response: AskResponse
  onFeedback?: (requestId: string, rating: 'up' | 'down') => Promise<void>
}

export function AskResponsePanel({ response, onFeedback }: AskResponsePanelProps) {
  const [feedback, setFeedback] = useState<'up' | 'down' | 'sent' | 'error' | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const hasMultipleExperts = response.routing.experts.length > 1
  const expertDomains = response.routing.experts.length
    ? response.routing.experts
    : response.experts.map((expert) => expert.expert)
  const rawLimitations = [
    ...response.gaps,
    ...response.experts.flatMap((expert) => expert.limitations),
  ].map((item) => item.trim()).filter(Boolean)
  const hasDateWarning = rawLimitations.some((item) =>
    /selected source text was retrieved on|selected source has no retrieval date/i.test(item),
  )
  const sourceDates = [...new Set(response.sources
    .map((source) => source.source.accessed_on)
    .filter((value): value is string => Boolean(value)))].sort()
  const uniqueLimitations = new Map<string, string>()
  for (const item of rawLimitations) {
    if (/selected source text was retrieved on|selected source has no retrieval date/i.test(item)) continue
    const key = item.replace(/\s+/g, ' ').toLowerCase()
    if (!uniqueLimitations.has(key)) uniqueLimitations.set(key, item)
  }
  const limitations = [...uniqueLimitations.values()]
  if (hasDateWarning) {
    limitations.push(sourceDates.length
      ? `Sources retrieved on ${sourceDates.join(', ')}. Check current official guidance for time-sensitive rules.`
      : 'Source retrieval dates are unavailable. Check current official guidance for time-sensitive rules.')
  }

  async function submitFeedback(rating: 'up' | 'down') {
    if (!onFeedback || submitting || feedback === 'sent') return
    setSubmitting(true)
    try {
      await onFeedback(response.request_id, rating)
      setFeedback('sent')
    } catch {
      setFeedback('error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="answer-card">
      <div className="answer-trust-row" aria-label="Experts and answer status">
        <div className="expert-chip-list" aria-label="Consulted experts">
          {expertDomains.map((domain) => {
            const expert = response.experts.find((item) => item.expert === domain)
            const isAligned = expert?.alignment_status === 'aligned'
            return (
              <span className="expert-trust-chip" key={domain}>
                <span className="expert-name-chip">{expertNames[domain]}</span>
                <StatusBadge tone={isAligned ? 'positive' : 'neutral'}>
                  {isAligned ? 'Aligned expert' : 'Base model'}
                </StatusBadge>
              </span>
            )
          })}
        </div>
        <StatusBadge tone={response.final_answer_state === 'validated' ? 'teal' : 'caution'}>
          {answerStateLabel(response.final_answer_state)}
        </StatusBadge>
      </div>

      {hasMultipleExperts && (
        <p className="consulted-line">Consulted: {response.routing.experts.map((expert) => expertNames[expert]).join(', ')}</p>
      )}

      <section className={`final-answer ${response.final_answer_state === 'withheld' ? 'withheld-answer' : ''}`} aria-label="Final answer">
        <h2 className="section-label">Answer</h2>
        {response.final_answer_mode === 'extractive' && response.final_answer_state === 'validated' && (
          <p className="extractive-label">Extractive summary from sources</p>
        )}
        {response.final_answer_mode === 'mixed' && response.final_answer_state === 'validated' && (
          <p className="extractive-label">This answer includes an extractive summary from sources.</p>
        )}
        {response.final_answer_state === 'validated' ? (
          <MarkdownContent className="answer-copy" text={response.final_answer} />
        ) : response.final_answer_state === 'withheld' ? (
          <div className="withheld-message" role="status">
            <strong>Source excerpts (no generated summary)</strong>
            <p>Retrieved passages are shown below because the generated summary could not be validated.</p>
          </div>
        ) : (
          <p className="no-source-message">No relevant source was found for this question.</p>
        )}

        <div className="feedback-row" aria-label="Answer feedback">
          <span>Was this helpful?</span>
          <button type="button" aria-label="Helpful" disabled={submitting || feedback === 'sent'} onClick={() => void submitFeedback('up')}>
            <ThumbsUp size={15} aria-hidden="true" />
          </button>
          <button type="button" aria-label="Not helpful" disabled={submitting || feedback === 'sent'} onClick={() => void submitFeedback('down')}>
            <ThumbsDown size={15} aria-hidden="true" />
          </button>
          {feedback === 'sent' && <span role="status">Feedback saved.</span>}
          {feedback === 'error' && <span role="status">Feedback could not be saved.</span>}
        </div>
      </section>

      <SourceList sources={response.sources} />

      <details className="answer-details">
        <summary>How this answer was made</summary>
        <div className="details-content">
          <section className="details-section">
            <h3>Routing</h3>
            <p>Decision: {response.routing.experts.length
              ? response.routing.experts.map((expert) => expertNames[expert]).join(', ')
              : 'No expert selected'}.</p>
            <p>Method: {routingMethodLabel(response.routing.routing_method)}</p>
            <p>Reason: {response.routing.reason}</p>
          </section>

          {response.experts.map((expert) => (
            <ExpertDetails expert={expert} key={expert.expert} />
          ))}

          {response.final_answer_claim_verifications.length > 0 && (
            <ClaimDetails title="Final answer claim checks" claims={response.final_answer_claim_verifications} />
          )}

          {limitations.length > 0 && (
            <section className="details-section">
              <h3>Limitations and gaps</h3>
              <ul>{limitations.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
            </section>
          )}

          <section className="details-section">
            <h3>Timing</h3>
            <ul className="timing-list">
              <li>Routing: {response.latency_ms.routing_ms} ms</li>
              <li>Experts: {response.latency_ms.experts_ms} ms</li>
              {Object.entries(response.latency_ms.per_expert_ms).map(([expert, milliseconds]) => (
                <li key={expert}>{expertNames[expert as Domain]}: {milliseconds} ms</li>
              ))}
              <li>Synthesis: {response.latency_ms.synthesis_ms} ms</li>
              <li>Total: {response.latency_ms.total_ms} ms</li>
            </ul>
          </section>
        </div>
      </details>
    </div>
  )
}

function answerStateLabel(state: AskResponse['final_answer_state']): string {
  if (state === 'validated') return 'Validated'
  if (state === 'withheld') return "Sources found; summary couldn't be validated"
  return 'No relevant source found'
}

function routingMethodLabel(method: AskResponse['routing']['routing_method']): string {
  if (method === 'model') return 'Automatic routing'
  if (method === 'fallback') return 'Fallback routing'
  return 'Selected experts'
}

function safeExpertError(expert: AskExpertResult): string {
  if (expert.status !== 'error') return ''
  if (/timeout|timed out/i.test(expert.error ?? '')) return 'This expert took too long to respond.'
  if (/source content unavailable/i.test(expert.error ?? '')) return 'Source content for this expert is unavailable.'
  return 'This expert could not complete the request.'
}

function ExpertDetails({ expert }: { expert: AskExpertResult }) {
  return (
    <section className="details-section expert-detail">
      <h3>{expertNames[expert.expert]} expert</h3>
      <dl className="model-metadata">
        <div><dt>Model ID</dt><dd>{expert.model_id || 'Not available'}</dd></div>
        <div><dt>Model mode</dt><dd>{modelModeLabel(expert.model_mode)}</dd></div>
        <div><dt>Alignment</dt><dd>{expert.alignment_status === 'aligned' ? 'Aligned' : 'Unaligned'}</dd></div>
        <div><dt>Citation repair retry</dt><dd>{expert.repair_retry_used ? 'Used' : 'Not used'}</dd></div>
      </dl>
      {expert.status === 'error' ? (
        <p className="expert-error">{safeExpertError(expert)}</p>
      ) : (
        <>
          <h4>{expert.answer_mode === 'extractive' ? 'Extractive summary from sources' : 'Expert response'}</h4>
          <MarkdownContent className="detail-answer-copy" text={expert.answer} />
          {expert.claim_verifications.length > 0 && (
            <ClaimDetails title="Expert claim checks" claims={expert.claim_verifications} />
          )}
        </>
      )}
    </section>
  )
}

function ClaimDetails({ title, claims }: { title: string; claims: ClaimVerification[] }) {
  const nonEmptyClaims = claims.filter((claim) => claim.claim_text.trim())
  if (nonEmptyClaims.length === 0) return null
  return (
    <section className="claim-details-block">
      <h4>{title}</h4>
      <ul>
        {nonEmptyClaims.map((claim, index) => (
          <li key={`${claim.claim_text}-${index}`}>
            <span className="claim-state">{claimStatusLabel(claim)}</span>
            <span>{claim.claim_text}</span>
            {claim.cited_evidence_ids.length > 0 && <span className="claim-citations">Sources: {claim.cited_evidence_ids.map((id) => `[${id}]`).join(', ')}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function claimStatusLabel(claim: ClaimVerification): string {
  if (claim.assessment_basis !== 'independent_verifier') return 'Not independently verified'
  if (claim.status === 'supported') return 'Verified by configured verifier'
  if (claim.status === 'contradicted') return 'Contradiction flagged by configured verifier'
  return 'Not independently verified'
}

function modelModeLabel(mode: AskExpertResult['model_mode']): string {
  if (mode === 'aligned') return 'Aligned model'
  if (mode === 'base_fallback_after_validation_failure') return 'Base fallback after validation failure'
  if (mode === 'base_fallback') return 'Base fallback'
  return 'Unavailable'
}
