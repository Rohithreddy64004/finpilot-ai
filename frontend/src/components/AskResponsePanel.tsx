import { useState } from 'react'
import { ChevronDown, ThumbsDown, ThumbsUp } from 'lucide-react'
import type { AskResponse } from '../types/api'
import { EvidencePanel } from './EvidencePanel'
import { MarkdownContent } from './MarkdownContent'
import { StatusBadge } from './StatusBadge'

const expertNames = {
  tax: 'Indian tax',
  investment: 'Investment',
  insurance: 'Insurance',
}

interface AskResponsePanelProps {
  response: AskResponse
  onFeedback?: (requestId: string, rating: 'up' | 'down') => Promise<void>
}

export function AskResponsePanel({ response, onFeedback }: AskResponsePanelProps) {
  const [feedback, setFeedback] = useState<'up' | 'down' | 'sent' | 'error' | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const finalClaims = response.final_answer_claim_verifications

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
    <div className="answer-card multi-answer-card">
      <div className="routing-summary">
        <div>
          <span className="panel-overline">ROUTING · {response.routing.routing_method}</span>
          <p><strong>{response.routing.experts.length
            ? response.routing.experts.map((expert) => expertNames[expert]).join(' + ')
            : 'No expert selected'}</strong></p>
        </div>
        <p>{response.routing.reason}</p>
      </div>

      {response.experts.length > 0 && (
        <section className="expert-results" aria-label="Expert results">
          <h2 className="section-label">Expert responses</h2>
          {response.experts.map((expert) => (
            <details className="expert-card" key={expert.expert} open={response.experts.length === 1}>
              <summary>
                <span>{expertNames[expert.expert]} expert</span>
                <span className="expert-summary-badges">
                  <StatusBadge tone={expert.status === 'error' ? 'caution' : expert.alignment_status === 'aligned' ? 'positive' : 'neutral'}>
                    {expert.status === 'error'
                      ? 'No model used'
                      : expert.model_mode === 'aligned'
                        ? 'Aligned model'
                        : expert.model_mode === 'base_fallback_after_validation_failure'
                          ? 'Base model after citation failure'
                          : 'Base model (not aligned)'}
                  </StatusBadge>
                  {expert.model_id && <code className="expert-model-id">{expert.model_id}</code>}
                  <ChevronDown className="details-chevron" size={15} />
                </span>
              </summary>
              {expert.status === 'error' ? (
                <p className="expert-error" role="status">{expertErrorMessage(expert.error)}</p>
              ) : (
                <div className="expert-card-body">
                  <div className="answer-topline">
                    <StatusBadge tone={expert.answer_state === 'validated' ? 'teal' : 'caution'}>
                      {answerStateLabel(expert.answer_state)}
                    </StatusBadge>
                    <StatusBadge tone="caution">{verificationLabel(expert.claim_verification_status)}</StatusBadge>
                    {expert.repair_retry_used && <span className="alignment-note">Citation repair retry used</span>}
                  </div>
                  <MarkdownContent className="answer-copy" text={expert.answer} />
                  {!expert.source_grounding_supported && (
                    <p className="answer-note">{expert.source_grounding_note}</p>
                  )}
                  <EvidencePanel evidence={expert.evidence} claims={expert.claim_verifications} />
                  {expert.limitations.length > 0 && (
                    <details className="limitations-details">
                      <summary>Limitations <span>{expert.limitations.length}</span></summary>
                      <ul>{expert.limitations.map((item, index) => (
                        <li key={item + index}><MarkdownContent className="markdown-limitation" text={item} linkCitations={false} /></li>
                      ))}</ul>
                    </details>
                  )}
                </div>
              )}
            </details>
          ))}
        </section>
      )}

      <section className="answer-section" aria-label="Final answer">
        <h2 className="section-label">FinPilot answer</h2>
        <div className="answer-topline">
          <StatusBadge tone={response.final_answer_state === 'validated' ? 'teal' : 'caution'}>
            {answerStateLabel(response.final_answer_state)}
          </StatusBadge>
          <StatusBadge tone={response.final_answer_independent_verification_performed ? 'positive' : 'caution'}>
            {verificationLabel(response.final_answer_claim_verification_status)}
          </StatusBadge>
        </div>
        <MarkdownContent className="answer-copy" text={response.final_answer} />
        {!response.final_answer_source_grounding_supported && (
          <p className="answer-note">{response.final_answer_source_grounding_note}</p>
        )}
        {response.final_excerpt_source_ids.length > 0 && (
          <FinalSourceExcerpts
            sources={response.sources.filter((source) => response.final_excerpt_source_ids.includes(source.citation_id))}
            allGeneratedWordingWithheld={response.final_answer_state === 'withheld'}
          />
        )}
        <p className="verification-origin">
          {response.final_answer_independent_verification_performed
            ? 'A separate configured verifier assessed claims against cited passages; this is not authoritative review.'
            : response.final_answer_claim_verification_note || 'No independent verifier ran; claims remain unverified.'}
        </p>
        <EvidencePanel evidence={response.sources} claims={finalClaims} />
      </section>

      {response.gaps.length > 0 && (
        <details className="limitations-details">
          <summary>Gaps and limitations <span>{response.gaps.length}</span></summary>
          <ul>{response.gaps.map((gap, index) => (
            <li key={gap + index}><MarkdownContent className="markdown-limitation" text={gap} linkCitations={false} /></li>
          ))}</ul>
        </details>
      )}

      <div className="answer-footer">
        <span>Routing {response.latency_ms.routing_ms} ms</span>
        <span>Experts {response.latency_ms.experts_ms} ms</span>
        <span>Total {response.latency_ms.total_ms} ms</span>
      </div>
      <div className="feedback-row" aria-label="Answer feedback">
        <span>Was this helpful?</span>
        <button type="button" aria-label="Helpful" disabled={submitting || feedback === 'sent'} onClick={() => void submitFeedback('up')}>
          <ThumbsUp size={15} />
        </button>
        <button type="button" aria-label="Not helpful" disabled={submitting || feedback === 'sent'} onClick={() => void submitFeedback('down')}>
          <ThumbsDown size={15} />
        </button>
        {feedback === 'sent' && <span role="status">Feedback saved.</span>}
        {feedback === 'error' && <span role="status">Feedback could not be saved.</span>}
      </div>
      <p className="disclaimer">Educational information only. This is not personalized or licensed financial advice.</p>
    </div>
  )
}

function answerStateLabel(state: AskResponse['final_answer_state']): string {
  if (state === 'validated') return 'Validated answer'
  if (state === 'withheld') return 'Sources found; summary could not be validated'
  return 'No supporting passage found'
}

function FinalSourceExcerpts({
  sources,
  allGeneratedWordingWithheld,
}: {
  sources: AskResponse['sources']
  allGeneratedWordingWithheld: boolean
}) {
  if (sources.length === 0) return null
  return (
    <section className="final-source-excerpts" aria-label="Retrieved source excerpts">
      <h3>{allGeneratedWordingWithheld
        ? 'Source excerpts (no generated summary)'
        : 'Source excerpts from experts without a validated answer'}</h3>
      {sources.map((source) => (
        <article className="final-source-excerpt" key={source.citation_id}>
          <div className="final-source-heading">
            <strong>{source.source.title}</strong>
            <span>{source.section_heading}</span>
          </div>
          <p>{source.excerpt}</p>
          <div className="final-source-meta">
            <span>{source.source.accessed_on ? `Retrieved ${source.source.accessed_on}` : 'Retrieved date unavailable'}</span>
            <SafeSourceUrl url={source.source.url ?? null} />
          </div>
        </article>
      ))}
    </section>
  )
}

function SafeSourceUrl({ url }: { url: string | null }) {
  if (!url) return <span>URL unavailable</span>
  let safeUrl: URL
  try {
    safeUrl = new URL(url)
  } catch {
    return <span>URL unavailable</span>
  }
  if ((safeUrl.protocol !== 'https:' && safeUrl.protocol !== 'http:') || safeUrl.username || safeUrl.password) {
    return <span>URL unavailable</span>
  }
  return <a href={safeUrl.href} target="_blank" rel="noopener noreferrer">{safeUrl.href}</a>
}

function verificationLabel(status: AskResponse['final_answer_claim_verification_status']): string {
  switch (status) {
    case 'fully_verified': return 'Separate verifier assessment'
    case 'partially_verified': return 'Verifier found mixed results'
    case 'evidence_supported': return 'Exact source wording matched'
    case 'partially_supported': return 'Some exact wording matched'
    case 'contains_contradiction': return 'Contradiction flagged'
    case 'verification_unavailable': return 'Verification unavailable'
    case 'unverified': return 'Unverified'
    case 'not_performed': return 'Not performed'
  }
}

function expertErrorMessage(error: string | null | undefined): string {
  const message = error || 'This expert could not complete the request.'
  if (/timeout|timed out|exceeded the configured request timeout/i.test(message)) {
    return /^expert timed out/i.test(message) ? message : `Expert timed out. ${message}`
  }
  if (/source content unavailable/i.test(message)) {
    return /^source content unavailable/i.test(message) ? message : `Source content unavailable. ${message}`
  }
  return message
}
