import type {
  ClaimVerification,
  Domain,
  InferenceRequest,
  InferenceResponse,
  RetrievedEvidence,
  SourceReference,
} from '../types/api'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/+$/, '')
const REQUEST_TIMEOUT_MS = 60_000

export class ApiError extends Error {
  readonly status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function requireString(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string' || !value.trim()) {
    throw new ApiError(`The backend response is missing a valid ${key} field.`)
  }
  return value
}

function optionalString(record: Record<string, unknown>, key: string): string | null | undefined {
  const value = record[key]
  if (value === undefined || value === null) return value
  if (typeof value !== 'string') {
    throw new ApiError(`The backend response contains an invalid ${key} field.`)
  }
  return value
}

function parseSourceReference(value: unknown): SourceReference {
  if (!isRecord(value)) throw new ApiError('The backend returned a malformed source reference.')
  const title = requireString(value, 'title')
  return {
    title,
    publisher: optionalString(value, 'publisher'),
    url: optionalString(value, 'url'),
    published_on: optionalString(value, 'published_on'),
    accessed_on: optionalString(value, 'accessed_on'),
  }
}

function parseEvidence(value: unknown): RetrievedEvidence {
  if (!isRecord(value)) throw new ApiError('The backend returned malformed evidence.')
  const citationId = requireString(value, 'citation_id')
  if (!/^S[1-9]\d*$/.test(citationId)) throw new ApiError('The backend returned an invalid evidence citation ID.')
  return {
    citation_id: citationId,
    passage_id: requireString(value, 'passage_id'),
    document_id: requireString(value, 'document_id'),
    document_name: requireString(value, 'document_name'),
    excerpt: requireString(value, 'excerpt'),
    source: parseSourceReference(value.source),
  }
}

function parseClaim(value: unknown): ClaimVerification {
  if (!isRecord(value)) throw new ApiError('The backend returned a malformed claim assessment.')
  const status = value.status
  if (status !== 'supported' && status !== 'contradicted' && status !== 'unverified') {
    throw new ApiError('The backend returned an unknown claim assessment status.')
  }
  const knownBases = ['independent_verifier', 'source_text_comparison', 'unavailable'] as const
  const assessmentBasis = value.assessment_basis === undefined
    ? 'unavailable'
    : value.assessment_basis
  if (!knownBases.includes(assessmentBasis as typeof knownBases[number])) {
    throw new ApiError('The backend returned an unknown claim assessment basis.')
  }
  const parseIds = (key: string): string[] => {
    const ids = value[key]
    if (!Array.isArray(ids) || !ids.every((id) => typeof id === 'string' && /^S[1-9]\d*$/.test(id))) {
      throw new ApiError(`The backend returned invalid ${key} for a claim assessment.`)
    }
    if (new Set(ids).size !== ids.length) {
      throw new ApiError(`The backend returned duplicate ${key} for a claim assessment.`)
    }
    return ids
  }
  const evidenceIds = parseIds('evidence_ids')
  const citedEvidenceIds = parseIds('cited_evidence_ids')
  const safeStatus = status !== 'unverified' && assessmentBasis === 'unavailable'
    ? 'unverified'
    : status
  const safeEvidenceIds = safeStatus === 'unverified' && assessmentBasis === 'unavailable'
    ? []
    : evidenceIds
  return {
    claim_text: requireString(value, 'claim_text'),
    status: safeStatus,
    assessment_basis: assessmentBasis as ClaimVerification['assessment_basis'],
    evidence_ids: safeEvidenceIds,
    cited_evidence_ids: citedEvidenceIds,
    note: optionalString(value, 'note') ?? '',
  }
}

export function parseInferenceResponse(value: unknown, expectedDomain: Domain): InferenceResponse {
  if (!isRecord(value)) throw new ApiError('The backend returned an invalid response.')
  const domain = value.domain
  if (domain !== expectedDomain) {
    throw new ApiError('The backend response did not match the selected finance domain.')
  }
  const alignmentStatus = value.alignment_status
  if (alignmentStatus !== 'aligned' && alignmentStatus !== 'unaligned') {
    throw new ApiError('The backend returned an unknown model alignment status.')
  }
  const parseArray = <T>(key: string, parseItem: (item: unknown) => T): T[] | undefined => {
    const items = value[key]
    if (items === undefined || items === null) return undefined
    if (!Array.isArray(items)) throw new ApiError(`The backend returned an invalid ${key} list.`)
    return items.map(parseItem)
  }
  const references = parseArray('source_references', parseSourceReference)
  const evidence = parseArray('evidence', parseEvidence)
  const claims = parseArray('claim_verifications', parseClaim)
  const limitations = value.limitations
  if (limitations !== undefined && limitations !== null &&
      (!Array.isArray(limitations) || !limitations.every((item) => typeof item === 'string'))) {
    throw new ApiError('The backend returned an invalid limitations list.')
  }
  const cleanedLimitations = Array.isArray(limitations)
    ? (limitations as string[]).filter((item) => item.trim().length > 0)
    : limitations
  const grounded = value.source_grounding_supported
  if (grounded !== undefined && grounded !== null && typeof grounded !== 'boolean') {
    throw new ApiError('The backend returned an invalid source grounding status.')
  }
  const claimStatus = value.claim_verification_status
  const knownClaimStatuses = [
    'fully_verified', 'partially_verified', 'evidence_supported', 'partially_supported',
    'verification_unavailable', 'unverified', 'contains_contradiction', 'not_performed',
  ]
  if (claimStatus !== undefined && claimStatus !== null &&
      !knownClaimStatuses.includes(String(claimStatus))) {
    throw new ApiError('The backend returned an unknown claim verification summary.')
  }
  const independent = value.independent_verification_performed
  if (independent !== undefined && independent !== null && typeof independent !== 'boolean') {
    throw new ApiError('The backend returned an invalid independent verification status.')
  }
  const independentVerificationPerformed = independent === true
  const normalizedClaims = claims ?? []
  const knownEvidenceIds = new Set((evidence ?? []).map((item) => item.citation_id))
  for (const claim of normalizedClaims) {
    if (claim.cited_evidence_ids.some((id) => !knownEvidenceIds.has(id)) ||
        claim.evidence_ids.some((id) => !claim.cited_evidence_ids.includes(id))) {
      throw new ApiError('The backend returned a claim assessment with an invalid evidence reference.')
    }
    if (claim.assessment_basis === 'independent_verifier' && !independentVerificationPerformed) {
      throw new ApiError('The backend returned independent claim assessments without confirming that verification ran.')
    }
    if (claim.assessment_basis === 'source_text_comparison' &&
        (claim.status !== 'supported' && claim.evidence_ids.length > 0 ||
         claim.status === 'supported' && claim.evidence_ids.length === 0)) {
      throw new ApiError('The backend returned an invalid source-text assessment.')
    }
    if (claim.assessment_basis !== 'independent_verifier' && claim.status === 'contradicted') {
      throw new ApiError('The backend returned a contradiction without a configured verifier assessment.')
    }
  }
  const hasIndependentClaim = normalizedClaims.some((claim) => claim.assessment_basis === 'independent_verifier')
  if (independentVerificationPerformed && !hasIndependentClaim) {
    throw new ApiError('The backend reported independent verification without any completed claim assessment.')
  }
  let normalizedClaimStatus = (claimStatus ?? undefined) as InferenceResponse['claim_verification_status']
  if (!independentVerificationPerformed &&
      (normalizedClaimStatus === 'fully_verified' || normalizedClaimStatus === 'partially_verified')) {
    normalizedClaimStatus = 'verification_unavailable'
  }
  if (normalizedClaimStatus === 'fully_verified' &&
      normalizedClaims.some((claim) => claim.status !== 'supported' || claim.assessment_basis !== 'independent_verifier')) {
    normalizedClaimStatus = 'unverified'
  }
  if (normalizedClaimStatus === 'evidence_supported' &&
      (normalizedClaims.length === 0 || normalizedClaims.some((claim) =>
        claim.status !== 'supported' || claim.assessment_basis !== 'source_text_comparison'))) {
    normalizedClaimStatus = 'verification_unavailable'
  }
  if (normalizedClaimStatus === 'contains_contradiction' &&
      !normalizedClaims.some((claim) => claim.status === 'contradicted' && claim.assessment_basis === 'independent_verifier')) {
    normalizedClaimStatus = 'unverified'
  }
  return {
    domain: expectedDomain,
    model_id: requireString(value, 'model_id'),
    alignment_status: alignmentStatus,
    answer: requireString(value, 'answer'),
    evidence,
    source_references: references,
    source_grounding_supported: grounded ?? undefined,
    source_grounding_note: optionalString(value, 'source_grounding_note') ?? undefined,
    claim_verifications: claims,
    claim_verification_status: normalizedClaimStatus,
    independent_verification_performed: independentVerificationPerformed,
    claim_verification_note: optionalString(value, 'claim_verification_note') ?? undefined,
    limitations: (cleanedLimitations as string[] | null | undefined) ?? undefined,
    applicable_as_of: optionalString(value, 'applicable_as_of') ?? undefined,
    educational_disclaimer: optionalString(value, 'educational_disclaimer') ?? undefined,
  }
}

function detailMessage(payload: unknown): string | undefined {
  if (!isRecord(payload)) return undefined
  const detail = payload.detail
  if (typeof detail === 'string') {
    if (/(traceback|stack trace|authorization\s*:|bearer\s+\S+|api[_ -]?key|mongodb(?:\+srv)?:\/\/)/i.test(detail)) {
      return undefined
    }
    return detail.slice(0, 500)
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) =>
      isRecord(item) && typeof item.msg === 'string' ? [item.msg] : [],
    )
    if (messages.length) return messages.slice(0, 4).join(' ')
  }
  return undefined
}

function errorForStatus(status: number, payload: unknown): string {
  if (status >= 500) {
    return 'FinPilot could not complete the request. Check that the backend is running and configured, then try again.'
  }
  return detailMessage(payload) ?? `The backend rejected the request (HTTP ${status}).`
}

export async function requestInference(request: InferenceRequest): Promise<InferenceResponse> {
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    let response: Response
    try {
      response = await fetch(`${API_BASE_URL}/api/inference`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
      })
    } catch {
      if (controller.signal.aborted) {
        throw new ApiError('The request timed out. Please try again.')
      }
      throw new ApiError('Cannot reach the FinPilot backend. Check the API URL and make sure the server is running.')
    }

    let responseText: string
    try {
      responseText = await response.text()
    } catch {
      if (controller.signal.aborted) throw new ApiError('The request timed out. Please try again.')
      throw new ApiError('The backend response could not be read. Please try again.')
    }
    let payload: unknown
    try {
      payload = responseText ? JSON.parse(responseText) : undefined
    } catch {
      if (response.ok) throw new ApiError('The backend returned malformed JSON.')
      payload = undefined
    }
    if (!response.ok) throw new ApiError(errorForStatus(response.status, payload), response.status)
    return parseInferenceResponse(payload, request.domain)
  } finally {
    globalThis.clearTimeout(timeout)
  }
}
