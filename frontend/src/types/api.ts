export type Domain = 'tax' | 'investment' | 'insurance'
export type AnswerState = 'validated' | 'no_passages' | 'withheld'
export type ModelMode = 'aligned' | 'base_fallback' | 'base_fallback_after_validation_failure'

export interface InferenceRequest {
  domain: Domain
  question: string
}

export interface SourceReference {
  title: string
  publisher?: string | null
  url?: string | null
  published_on?: string | null
  accessed_on?: string | null
}

export interface RetrievedEvidence {
  citation_id: string
  passage_id: string
  document_id: string
  document_name: string
  section_heading: string
  excerpt: string
  source: SourceReference
}

export type ClaimStatus = 'supported' | 'contradicted' | 'unverified'
export type ClaimAssessmentBasis = 'independent_verifier' | 'source_text_comparison' | 'unavailable'

export interface ClaimVerification {
  claim_text: string
  status: ClaimStatus
  assessment_basis: ClaimAssessmentBasis
  evidence_ids: string[]
  cited_evidence_ids: string[]
  note: string
}

export type ClaimVerificationSummaryStatus =
  | 'fully_verified'
  | 'partially_verified'
  | 'evidence_supported'
  | 'partially_supported'
  | 'verification_unavailable'
  | 'unverified'
  | 'contains_contradiction'
  | 'not_performed'

export interface InferenceResponse {
  domain: Domain
  model_id: string
  model_mode: ModelMode
  alignment_status: 'aligned' | 'unaligned'
  answer: string
  answer_state: AnswerState
  repair_retry_used: boolean
  evidence?: RetrievedEvidence[]
  source_references?: SourceReference[]
  source_grounding_supported?: boolean
  source_grounding_note?: string
  claim_verifications?: ClaimVerification[]
  claim_verification_status?: ClaimVerificationSummaryStatus
  independent_verification_performed?: boolean
  claim_verification_note?: string
  limitations?: string[]
  applicable_as_of?: string
  educational_disclaimer?: string
}

export interface AskRequest {
  question: string
  experts?: Domain[]
}

export interface SourceDomainHealth {
  domain: Domain
  active_document_ids: string[]
  status: 'ready' | 'missing' | 'invalid'
  message: string
  issues: string[]
}

export interface SourceHealthResponse {
  domains: SourceDomainHealth[]
}

export interface AskSource extends RetrievedEvidence {
  expert: Domain
}

export interface AskExpertResult {
  expert: Domain
  status: 'complete' | 'error'
  answer: string
  model_id: string | null
  model_mode: ModelMode | null
  alignment_status: 'aligned' | 'unaligned'
  answer_state: AnswerState
  repair_retry_used: boolean
  source_grounding_supported: boolean
  source_grounding_note: string
  evidence: AskSource[]
  claim_verifications: ClaimVerification[]
  claim_verification_status: ClaimVerificationSummaryStatus
  independent_verification_performed: boolean
  claim_verification_note: string
  limitations: string[]
  error?: string | null
}

export interface AskResponse {
  request_id: string
  question: string
  routing: {
    experts: Domain[]
    reason: string
    routing_method: 'model' | 'fallback' | 'override'
  }
  experts: AskExpertResult[]
  final_answer: string
  final_answer_state: AnswerState
  final_excerpt_source_ids: string[]
  final_answer_source_grounding_supported: boolean
  final_answer_source_grounding_note: string
  final_answer_claim_verifications: ClaimVerification[]
  final_answer_claim_verification_status: ClaimVerificationSummaryStatus
  final_answer_independent_verification_performed: boolean
  final_answer_claim_verification_note: string
  sources: AskSource[]
  gaps: string[]
  latency_ms: {
    routing_ms: number
    experts_ms: number
    per_expert_ms: Partial<Record<Domain, number>>
    synthesis_ms: number
    total_ms: number
  }
}

export interface FeedbackResponse {
  id: string
  query_id: string
  notice: string
}
