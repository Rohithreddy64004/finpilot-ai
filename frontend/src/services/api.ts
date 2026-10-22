import type {
  AskExpertResult,
  AskRequest,
  AskResponse,
  AskSource,
  AnswerState,
  ClaimVerification,
  ClaimVerificationSummaryStatus,
  Domain,
  FeedbackResponse,
  InferenceRequest,
  InferenceResponse,
  ModelMode,
  RetrievedEvidence,
  SourceHealthResponse,
  SourceReference,
} from '../types/api'

// Keep browser requests same-origin; Vite proxies these paths in local dev.
const API_BASE_URL = ''
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
    section_heading: requireString(value, 'section_heading'),
    excerpt: requireString(value, 'excerpt'),
    source: parseSourceReference(value.source),
  }
}

function parseAnswerState(value: unknown): AnswerState {
  if (value !== 'validated' && value !== 'no_passages' && value !== 'withheld') {
    throw new ApiError('The backend returned an unknown answer state.')
  }
  return value
}

function parseModelMode(value: unknown): ModelMode {
  if (value !== 'aligned' && value !== 'base_fallback' && value !== 'base_fallback_after_validation_failure') {
    throw new ApiError('The backend returned an unknown model mode.')
  }
  return value
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
  const modelMode = parseModelMode(value.model_mode)
  const answerState = parseAnswerState(value.answer_state)
  if (typeof value.repair_retry_used !== 'boolean') {
    throw new ApiError('The backend returned an invalid citation repair retry status.')
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
    model_mode: modelMode,
    alignment_status: alignmentStatus,
    answer: requireString(value, 'answer'),
    answer_state: answerState,
    repair_retry_used: value.repair_retry_used,
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
  if (status === 429) {
    return 'FinPilot is rate limited (HTTP 429). Wait briefly before sending another question.'
  }
  if (status === 503) {
    const safeDetail = detailMessage(payload)
    if (safeDetail && /source content unavailable/i.test(safeDetail)) {
      return 'Source content unavailable (HTTP 503). Restore the verified local source text, then retry.'
    }
    return 'FinPilot service is not ready (HTTP 503). Check the active source set and server configuration.'
  }
  if (status === 504) {
    return 'The FinPilot or Nugen request timed out (HTTP 504). Please try again.'
  }
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
      throw new ApiError('Cannot reach FinPilot through the same-origin API path. Check that the backend server is running.')
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

function parseAskSource(value: unknown): AskSource {
  if (!isRecord(value)) throw new ApiError('The backend returned malformed ask evidence.')
  const expert = value.expert
  if (expert !== 'tax' && expert !== 'investment' && expert !== 'insurance') {
    throw new ApiError('The backend returned an unknown expert for a source passage.')
  }
  return { ...parseEvidence(value), expert }
}

function parseStringList(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) {
    throw new ApiError('The backend returned an invalid ' + field + ' list.')
  }
  return value
}

function parseAskExpert(value: unknown): AskExpertResult {
  if (!isRecord(value)) throw new ApiError('The backend returned a malformed expert result.')
  const expert = value.expert
  if (expert !== 'tax' && expert !== 'investment' && expert !== 'insurance') {
    throw new ApiError('The backend returned an unknown expert.')
  }
  const status = value.status
  const alignment = value.alignment_status
  const answerState = parseAnswerState(value.answer_state)
  const modelMode = status === 'error' && value.model_mode === null
    ? null
    : parseModelMode(value.model_mode)
  if (typeof value.repair_retry_used !== 'boolean') {
    throw new ApiError('The backend returned an invalid expert citation repair retry status.')
  }
  const grounded = value.source_grounding_supported
  const independent = value.independent_verification_performed
  if (status !== 'complete' && status !== 'error') throw new ApiError('The backend returned an invalid expert state.')
  if (alignment !== 'aligned' && alignment !== 'unaligned') throw new ApiError('The backend returned an invalid alignment state.')
  if (typeof grounded !== 'boolean' || typeof independent !== 'boolean') {
    throw new ApiError('The backend returned invalid expert verification metadata.')
  }
  if (!Array.isArray(value.evidence) || !Array.isArray(value.claim_verifications)) {
    throw new ApiError('The backend returned malformed expert evidence or claim assessments.')
  }
  const evidence = value.evidence.map(parseAskSource)
  if (evidence.some((source) => source.expert !== expert)) {
    throw new ApiError('The backend mixed source passages across expert domains.')
  }
  const knownIds = new Set(evidence.map((item) => item.citation_id))
  const claims = value.claim_verifications.map(parseClaim)
  for (const claim of claims) {
    if (claim.cited_evidence_ids.some((id) => !knownIds.has(id)) ||
        claim.evidence_ids.some((id) => !claim.cited_evidence_ids.includes(id))) {
      throw new ApiError('The backend returned a claim linked to unavailable expert evidence.')
    }
  }
  const answer = requireString(value, 'answer')
  const modelId = status === 'complete'
    ? requireString(value, 'model_id')
    : optionalString(value, 'model_id') ?? null
  const answerMarkers = [...answer.matchAll(/\[S[^\]\r\n]*\]/g)].map((match) => match[0])
  const answerIds = answerMarkers.map((marker) => /^\[(S\d+)\]$/.exec(marker)?.[1])
  if (answerIds.some((id) => !id || !knownIds.has(id)) ||
      (grounded && answerState === 'validated' && answerIds.length === 0)) {
    throw new ApiError('The backend returned an expert answer with invalid source markers.')
  }
  if (independent && !claims.some((claim) => claim.assessment_basis === 'independent_verifier')) {
    throw new ApiError('The backend reported expert verification without a verifier assessment.')
  }
  return {
    expert,
    status,
    answer,
    model_id: modelId,
    model_mode: modelMode,
    alignment_status: alignment,
    answer_state: answerState,
    repair_retry_used: value.repair_retry_used,
    source_grounding_supported: grounded,
    source_grounding_note: optionalString(value, 'source_grounding_note') ?? '',
    evidence,
    claim_verifications: claims,
    claim_verification_status: parseAskClaimSummary(value.claim_verification_status),
    independent_verification_performed: independent,
    claim_verification_note: optionalString(value, 'claim_verification_note') ?? '',
    limitations: parseStringList(value.limitations, 'expert limitations'),
    error: optionalString(value, 'error'),
  }
}

function parseAskClaimSummary(value: unknown): ClaimVerificationSummaryStatus {
  const known = [
    'fully_verified', 'partially_verified', 'evidence_supported', 'partially_supported',
    'verification_unavailable', 'unverified', 'contains_contradiction', 'not_performed',
  ]
  if (typeof value !== 'string' || !known.includes(value)) {
    throw new ApiError('The backend returned an unknown claim verification summary.')
  }
  return value as ClaimVerificationSummaryStatus
}

function parseNonnegativeInteger(value: unknown, field: string): number {
  if (!Number.isInteger(value) || Number(value) < 0) {
    throw new ApiError('The backend returned an invalid ' + field + '.')
  }
  return Number(value)
}

export function parseAskResponse(value: unknown): AskResponse {
  if (!isRecord(value) || !isRecord(value.routing) || !isRecord(value.latency_ms)) {
    throw new ApiError('The backend returned an invalid multi-expert response.')
  }
  const routingExperts = value.routing.experts
  if (!Array.isArray(routingExperts) ||
      !routingExperts.every((item) => item === 'tax' || item === 'investment' || item === 'insurance')) {
    throw new ApiError('The backend returned an invalid routing decision.')
  }
  const routingMethod = value.routing.routing_method
  if (routingMethod !== 'model' && routingMethod !== 'fallback' && routingMethod !== 'override') {
    throw new ApiError('The backend returned an unknown routing method.')
  }
  if (!Array.isArray(value.experts) || !Array.isArray(value.sources) ||
      !Array.isArray(value.final_answer_claim_verifications)) {
    throw new ApiError('The backend returned invalid expert or source lists.')
  }
  const experts = value.experts.map(parseAskExpert)
  const sources = value.sources.map(parseAskSource)
  const sourceIds = sources.map((source) => source.citation_id)
  if (new Set(sourceIds).size !== sourceIds.length) {
    throw new ApiError('The backend returned duplicate source citation IDs.')
  }
  const knownIds = new Set(sourceIds)
  const finalAnswerState = parseAnswerState(value.final_answer_state)
  const finalExcerptSourceIds = parseStringList(value.final_excerpt_source_ids, 'final excerpt source ID')
  if (new Set(finalExcerptSourceIds).size !== finalExcerptSourceIds.length ||
      finalExcerptSourceIds.some((id) => !knownIds.has(id))) {
    throw new ApiError('The backend returned an invalid final source excerpt reference.')
  }
  const sourceById = new Map(sources.map((source) => [source.citation_id, source]))
  for (const expert of experts) {
    for (const evidence of expert.evidence) {
      const returnedSource = sourceById.get(evidence.citation_id)
      if (!returnedSource || returnedSource.expert !== expert.expert ||
          returnedSource.passage_id !== evidence.passage_id) {
        throw new ApiError('The backend returned inconsistent expert and final source records.')
      }
    }
  }
  const claims = value.final_answer_claim_verifications.map(parseClaim)
  for (const claim of claims) {
    if (claim.cited_evidence_ids.some((id) => !knownIds.has(id)) ||
        claim.evidence_ids.some((id) => !claim.cited_evidence_ids.includes(id))) {
      throw new ApiError('The final claim assessment references a missing source passage.')
    }
  }
  const finalAnswer = requireString(value, 'final_answer')
  const grounded = value.final_answer_source_grounding_supported
  const independent = value.final_answer_independent_verification_performed
  if (typeof grounded !== 'boolean' || typeof independent !== 'boolean') {
    throw new ApiError('The backend returned invalid final-answer verification metadata.')
  }
  const citationLike = [...finalAnswer.matchAll(/\[S[^\]\r\n]*\]/g)].map((match) => match[0])
  const finalIds = citationLike.map((marker) => /^\[(S\d+)\]$/.exec(marker)?.[1])
  if (finalIds.some((id) => !id || !knownIds.has(id)) ||
      (grounded && finalAnswerState === 'validated' && finalIds.length === 0)) {
    throw new ApiError('The backend returned a final answer with invalid source markers.')
  }
  const perExpert = value.latency_ms.per_expert_ms
  if (!isRecord(perExpert)) throw new ApiError('The backend returned invalid expert timing data.')
  const perExpertMs: Partial<Record<Domain, number>> = {}
  for (const [key, timing] of Object.entries(perExpert)) {
    if (key !== 'tax' && key !== 'investment' && key !== 'insurance') {
      throw new ApiError('The backend returned a timing value for an unknown expert.')
    }
    perExpertMs[key] = parseNonnegativeInteger(timing, 'expert latency')
  }
  return {
    request_id: requireString(value, 'request_id'),
    question: requireString(value, 'question'),
    routing: {
      experts: routingExperts as Domain[],
      reason: requireString(value.routing, 'reason'),
      routing_method: routingMethod,
    },
    experts,
    final_answer: finalAnswer,
    final_answer_state: finalAnswerState,
    final_excerpt_source_ids: finalExcerptSourceIds,
    final_answer_source_grounding_supported: grounded,
    final_answer_source_grounding_note: optionalString(value, 'final_answer_source_grounding_note') ?? '',
    final_answer_claim_verifications: claims,
    final_answer_claim_verification_status: parseAskClaimSummary(value.final_answer_claim_verification_status),
    final_answer_independent_verification_performed: independent,
    final_answer_claim_verification_note: optionalString(value, 'final_answer_claim_verification_note') ?? '',
    sources,
    gaps: parseStringList(value.gaps, 'gap'),
    latency_ms: {
      routing_ms: parseNonnegativeInteger(value.latency_ms.routing_ms, 'routing latency'),
      experts_ms: parseNonnegativeInteger(value.latency_ms.experts_ms, 'expert latency'),
      per_expert_ms: perExpertMs,
      synthesis_ms: parseNonnegativeInteger(value.latency_ms.synthesis_ms, 'synthesis latency'),
      total_ms: parseNonnegativeInteger(value.latency_ms.total_ms, 'total latency'),
    },
  }
}

async function postJson(path: string, body: unknown, timeoutMs = REQUEST_TIMEOUT_MS): Promise<unknown> {
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs)
  try {
    let response: Response
    try {
      response = await fetch(API_BASE_URL + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
    } catch {
      if (controller.signal.aborted) throw new ApiError('The request timed out. Please try again.')
      throw new ApiError('Cannot reach FinPilot through the same-origin API path. Check that the backend server is running.')
    }
    const responseText = await response.text()
    let payload: unknown
    try {
      payload = responseText ? JSON.parse(responseText) : undefined
    } catch {
      if (response.ok) throw new ApiError('The backend returned malformed JSON.')
    }
    if (!response.ok) throw new ApiError(errorForStatus(response.status, payload), response.status)
    return payload
  } finally {
    globalThis.clearTimeout(timeout)
  }
}

export async function requestAsk(request: AskRequest): Promise<AskResponse> {
  return parseAskResponse(await postJson('/api/ask', request, 180_000))
}

export async function requestSourceHealth(): Promise<SourceHealthResponse> {
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    let response: Response
    try {
      response = await fetch('/api/source-health', {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
    } catch {
      if (controller.signal.aborted) throw new ApiError('The source status check timed out. Please retry.')
      throw new ApiError('Cannot reach FinPilot through the same-origin API path. Check that the backend server is running.')
    }
    const responseText = await response.text()
    let payload: unknown
    try {
      payload = responseText ? JSON.parse(responseText) : undefined
    } catch {
      if (response.ok) throw new ApiError('The backend returned malformed source status data.')
    }
    if (!response.ok) throw new ApiError(errorForStatus(response.status, payload), response.status)
    if (!isRecord(payload) || !Array.isArray(payload.domains)) {
      throw new ApiError('The backend returned invalid active source status data.')
    }
    const domains = payload.domains.map((item) => {
      if (!isRecord(item) ||
          (item.domain !== 'tax' && item.domain !== 'investment' && item.domain !== 'insurance') ||
          (item.status !== 'ready' && item.status !== 'missing' && item.status !== 'invalid') ||
          typeof item.message !== 'string' || !Array.isArray(item.active_document_ids) ||
          !item.active_document_ids.every((id) => typeof id === 'string') ||
          !Array.isArray(item.issues) || !item.issues.every((issue) => typeof issue === 'string')) {
        throw new ApiError('The backend returned an invalid active source status entry.')
      }
      return {
        domain: item.domain as Domain,
        active_document_ids: item.active_document_ids as string[],
        status: item.status as SourceHealthResponse['domains'][number]['status'],
        message: item.message,
        issues: item.issues as string[],
      }
    })
    return { domains }
  } finally {
    globalThis.clearTimeout(timeout)
  }
}

export async function requestFeedback(queryId: string, rating: 'up' | 'down'): Promise<FeedbackResponse> {
  const payload = await postJson('/api/feedback', { query_id: queryId, rating })
  if (!isRecord(payload)) throw new ApiError('The backend returned an invalid feedback response.')
  return {
    id: requireString(payload, 'id'),
    query_id: requireString(payload, 'query_id'),
    notice: optionalString(payload, 'notice') ?? '',
  }
}
