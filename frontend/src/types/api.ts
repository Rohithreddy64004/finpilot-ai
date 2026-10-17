export type Domain = 'tax' | 'investment' | 'insurance'

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
  alignment_status: 'aligned' | 'unaligned'
  answer: string
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
