import { describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  parseAskResponse,
  parseInferenceResponse,
  requestAsk,
  requestFeedback,
  requestInference,
  requestSourceHealth,
} from './api'
import type { AskResponse, Domain, InferenceResponse } from '../types/api'

function responseFixture(domain: Domain = 'tax'): InferenceResponse {
  return {
    domain,
    model_id: 'fixture-base-model',
    model_mode: 'base_fallback',
    alignment_status: 'unaligned',
    answer: 'A deduction reduces taxable income. [S1]',
    answer_state: 'validated',
    answer_mode: 'generated',
    repair_retry_used: false,
    evidence: [{
      citation_id: 'S1',
      passage_id: 'tax:document-fixture:1',
      document_id: 'document-fixture',
      document_name: 'Tax guide',
      section_heading: 'Tax deductions',
      excerpt: 'A deduction reduces taxable income.',
      source: {
        title: 'Income Tax Department guide',
        publisher: 'Income Tax Department',
        url: 'https://www.incometax.gov.in/guide',
        accessed_on: '2026-10-15',
      },
    }],
    source_references: [],
    source_grounding_supported: true,
    source_grounding_note: 'The answer cites a retrieved passage.',
    claim_verifications: [],
    claim_verification_status: 'unverified',
    independent_verification_performed: false,
    claim_verification_note: 'No independent verifier is configured.',
    limitations: [],
    applicable_as_of: '2026-10-16',
    educational_disclaimer: 'General education only.',
  }
}

function mockedResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  } as Response
}

function askResponseFixture(): AskResponse {
  const source = {
    expert: 'tax' as const,
    citation_id: 'S1',
    passage_id: 'tax:passage:1',
    document_id: 'tax-document',
    document_name: 'Tax guide',
    section_heading: 'Tax regimes',
    excerpt: 'Taxable income is assessed under the applicable regime.',
    source: { title: 'Income Tax Department', url: 'https://www.incometax.gov.in/' },
  }
  return {
    request_id: 'query-1',
    question: 'How do tax regimes work?',
    routing: { experts: ['tax'], reason: 'Tax topic.', routing_method: 'fallback' },
    experts: [{
      expert: 'tax',
      status: 'complete',
      answer: 'Taxable income is assessed under the applicable regime. [S1]',
      model_id: 'fixture-base-model',
      model_mode: 'base_fallback',
      alignment_status: 'unaligned',
      answer_state: 'validated',
      answer_mode: 'generated',
      repair_retry_used: false,
      source_grounding_supported: true,
      source_grounding_note: 'The citation maps to a retrieved passage.',
      evidence: [source],
      claim_verifications: [],
      claim_verification_status: 'verification_unavailable',
      independent_verification_performed: false,
      claim_verification_note: 'No verifier is configured.',
      limitations: [],
      error: null,
    }],
    final_answer: 'Taxable income is assessed under the applicable regime. [S1]',
    final_answer_state: 'validated',
    final_answer_mode: 'generated',
    final_excerpt_source_ids: [],
    final_answer_source_grounding_supported: true,
    final_answer_source_grounding_note: 'The citation maps to a retrieved passage.',
    final_answer_claim_verifications: [],
    final_answer_claim_verification_status: 'verification_unavailable',
    final_answer_independent_verification_performed: false,
    final_answer_claim_verification_note: 'No verifier is configured.',
    sources: [source],
    gaps: [],
    latency_ms: { routing_ms: 2, experts_ms: 3, per_expert_ms: { tax: 2 }, synthesis_ms: 0, total_ms: 5 },
  }
}

describe('requestInference', () => {
  it('posts the backend request shape and parses source metadata', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockedResponse(responseFixture('investment')))
    vi.stubGlobal('fetch', fetchMock)

    const response = await requestInference({ domain: 'investment', question: 'Explain diversification.' })

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/inference$/)
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', Accept: 'application/json' })
    expect(JSON.parse(String(init.body))).toEqual({ domain: 'investment', question: 'Explain diversification.' })
    expect(response.evidence?.[0].source.publisher).toBe('Income Tax Department')
    expect(response.claim_verification_status).toBe('unverified')
  })

  it.each([
    ['configured', 'https://api.example.test', 'https://api.example.test/api/ask'],
    ['configured with trailing slash', 'https://api.example.test///', 'https://api.example.test/api/ask'],
    ['unset', undefined, '/api/ask'],
  ])('uses the API base URL when %s', async (_label, baseUrl, expectedUrl) => {
    vi.stubEnv('VITE_API_BASE_URL', baseUrl)
    vi.resetModules()
    const api = await import('./api')
    const fetchMock = vi.fn().mockResolvedValue(mockedResponse(askResponseFixture()))
    vi.stubGlobal('fetch', fetchMock)

    await api.requestAsk({ question: 'How do tax regimes work?' })

    expect(fetchMock.mock.calls[0][0]).toBe(expectedUrl)
  })

  it('shows safe validation details from FastAPI', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockedResponse({
      detail: [{ loc: ['body', 'question'], msg: 'Field required', type: 'missing' }],
    }, 422)))

    await expect(requestInference({ domain: 'tax', question: ' ' })).rejects.toMatchObject({
      name: 'ApiError',
      status: 422,
      message: 'Field required',
    })
  })

  it('does not expose raw server error bodies or stack details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockedResponse({
      detail: 'secret-looking token or internal traceback',
    }, 503)))

    await expect(requestInference({ domain: 'insurance', question: 'Explain a waiting period.' })).rejects.toThrow(
      'FinPilot service is not ready (HTTP 503). Check the active source set and server configuration.',
    )
  })

  it('does not render credential-like details from client errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockedResponse({
      detail: 'Authorization: Bearer fake-secret-value',
    }, 400)))

    await expect(requestInference({ domain: 'tax', question: 'Explain a rebate.' })).rejects.toThrow(
      'The backend rejected the request (HTTP 400).',
    )
  })

  it('reports a backend that cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network failure')))

    await expect(requestInference({ domain: 'tax', question: 'Explain a tax rebate.' })).rejects.toThrow(
      'Cannot reach FinPilot through the same-origin API path.',
    )
  })

  it('distinguishes unavailable source content, service readiness, rate limits, and upstream timeout', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(mockedResponse({
      detail: 'Source content unavailable: registered tax source text is missing.',
    }, 503)))
    await expect(requestInference({ domain: 'tax', question: 'Explain tax slabs.' })).rejects.toThrow(
      'Source content unavailable (HTTP 503). Restore the verified local source text, then retry.',
    )

    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(mockedResponse({ detail: 'not ready' }, 503)))
    await expect(requestInference({ domain: 'tax', question: 'Explain tax slabs.' })).rejects.toThrow(
      'FinPilot service is not ready (HTTP 503). Check the active source set and server configuration.',
    )

    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(mockedResponse({ detail: 'slow down' }, 429)))
    await expect(requestInference({ domain: 'tax', question: 'Explain tax slabs.' })).rejects.toThrow(
      'FinPilot is rate limited (HTTP 429). Wait briefly before sending another question.',
    )

    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(mockedResponse({ detail: 'timeout' }, 504)))
    await expect(requestInference({ domain: 'tax', question: 'Explain tax slabs.' })).rejects.toThrow(
      'The FinPilot or Nugen request timed out (HTTP 504). Please try again.',
    )
  })

  it('turns an aborted request into a clear timeout message', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const pending = requestInference({ domain: 'tax', question: 'Explain a tax rebate.' })
    const assertion = expect(pending).rejects.toThrow('The request timed out. Please try again.')
    await vi.advanceTimersByTimeAsync(60_000)

    await assertion
    vi.useRealTimers()
  })
})

describe('multi-expert API', () => {
  it('posts an ask with an optional expert override and validates citations', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockedResponse(askResponseFixture()))
    vi.stubGlobal('fetch', fetchMock)

    const response = await requestAsk({ question: 'How do tax regimes work?', experts: ['tax'] })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/ask$/)
    expect(JSON.parse(String(init.body))).toEqual({
      question: 'How do tax regimes work?',
      experts: ['tax'],
    })
    expect(response.routing.experts).toEqual(['tax'])
    expect(response.sources[0].citation_id).toBe('S1')
    expect(response.experts[0].model_id).toBe('fixture-base-model')
  })

  it('shows no model identity for an expert result that used no model', () => {
    const fixture = askResponseFixture()
    fixture.experts[0].status = 'error'
    fixture.experts[0].model_id = null
    fixture.experts[0].model_mode = null
    fixture.experts[0].error = 'No model was selected.'

    expect(parseAskResponse(fixture).experts[0].model_id).toBeNull()
  })

  it('keeps rate-limit, service, upstream-timeout, network, and local-timeout messages distinct', async () => {
    const cases = [
      [429, 'FinPilot is rate limited (HTTP 429). Wait briefly before sending another question.'],
      [503, 'FinPilot service is not ready (HTTP 503). Check the active source set and server configuration.'],
      [504, 'The FinPilot or Nugen request timed out (HTTP 504). Please try again.'],
    ] as const
    for (const [status, message] of cases) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockedResponse({ detail: 'safe' }, status)))
      await expect(requestAsk({ question: 'Explain a tax slab.' })).rejects.toMatchObject({ message, status })
    }

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network failure')))
    await expect(requestAsk({ question: 'Explain a tax slab.' })).rejects.toThrow(
      'Cannot reach FinPilot through the same-origin API path.',
    )

    vi.useFakeTimers()
    const fetchMock = vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const pending = requestAsk({ question: 'Explain a tax slab.' })
    const assertion = expect(pending).rejects.toThrow('The request timed out. Please try again.')
    await vi.advanceTimersByTimeAsync(180_000)
    await assertion
    vi.useRealTimers()
  })

  it('loads active source quality states for the router-first source banner', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockedResponse({
      domains: [
        { domain: 'tax', active_document_ids: [], status: 'missing', message: 'Register a source.', issues: [] },
        { domain: 'investment', active_document_ids: ['old'], status: 'invalid', message: 'Review the source.', issues: ['bad heading'] },
        { domain: 'insurance', active_document_ids: ['ready'], status: 'ready', message: 'Ready.', issues: [] },
      ],
    })))

    const response = await requestSourceHealth()

    expect(response.domains.map((item) => item.status)).toEqual(['missing', 'invalid', 'ready'])
  })

  it('rejects a grounded answer whose citation is absent from returned sources', () => {
    const fixture = askResponseFixture()
    fixture.final_answer = 'Unsupported citation [S9].'
    expect(() => parseAskResponse(fixture)).toThrow(/invalid source markers/)
  })

  it('rejects malformed citation markers in an individual expert answer', () => {
    const fixture = askResponseFixture()
    fixture.experts[0].answer = 'An answer with a malformed marker [Sx].'
    expect(() => parseAskResponse(fixture)).toThrow(/expert answer with invalid source markers/)
  })

  it('accepts a withheld expert summary with its retrieved source excerpts', () => {
    const fixture = askResponseFixture()
    fixture.experts[0].answer = 'Generated wording was withheld.'
    fixture.experts[0].answer_state = 'withheld'
    fixture.final_answer = 'Source excerpts (no generated summary)'
    fixture.final_answer_state = 'withheld'
    fixture.final_answer_source_grounding_supported = false
    fixture.final_excerpt_source_ids = ['S1']

    expect(parseAskResponse(fixture).final_excerpt_source_ids).toEqual(['S1'])
  })

  it('accepts the out-of-scope final answer state without sources or experts', () => {
    const fixture = askResponseFixture()
    fixture.routing.experts = []
    fixture.experts = []
    fixture.sources = []
    fixture.final_answer = 'I can help with Indian tax, investing, and insurance questions.'
    fixture.final_answer_state = 'out_of_scope'
    fixture.final_answer_mode = 'none'
    fixture.final_answer_source_grounding_supported = false

    expect(parseAskResponse(fixture).final_answer_state).toBe('out_of_scope')
  })

  it('posts feedback against the saved query identifier', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockedResponse({
      id: 'feedback-1',
      query_id: 'query-1',
      notice: 'Stored separately.',
    }, 201))
    vi.stubGlobal('fetch', fetchMock)

    await requestFeedback('query-1', 'up')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/feedback$/)
    expect(JSON.parse(String(init.body))).toEqual({ query_id: 'query-1', rating: 'up' })
  })
})

describe('parseInferenceResponse', () => {
  it('handles absent optional display fields without failing', () => {
    const payload = {
      domain: 'tax',
      model_id: 'fixture-base-model',
      model_mode: 'base_fallback',
      alignment_status: 'unaligned',
      answer: 'General educational answer.',
      answer_state: 'validated',
      answer_mode: 'generated',
      repair_retry_used: false,
    }

    expect(parseInferenceResponse(payload, 'tax')).toMatchObject({
      answer: 'General educational answer.',
      evidence: undefined,
      source_references: undefined,
    })
  })

  it('rejects malformed and cross-domain responses', () => {
    expect(() => parseInferenceResponse({ answer: 'Missing required metadata.' }, 'tax')).toThrow(ApiError)
    expect(() => parseInferenceResponse(responseFixture('investment'), 'tax')).toThrow(/selected finance domain/)
  })

  it('fails closed for legacy success labels without independent verification metadata', () => {
    const legacyResponse = {
      ...responseFixture(),
      claim_verification_status: 'fully_verified',
      independent_verification_performed: undefined,
    }
    const parsed = parseInferenceResponse(legacyResponse, 'tax')

    expect(parsed.independent_verification_performed).toBe(false)
    expect(parsed.claim_verification_status).toBe('verification_unavailable')
  })

  it('rejects a claim that cites evidence absent from the response evidence list', () => {
    const response = responseFixture() as InferenceResponse & Record<string, unknown>
    response.claim_verifications = [{
      claim_text: 'Unsupported reference.',
      status: 'unverified',
      assessment_basis: 'unavailable',
      evidence_ids: [],
      cited_evidence_ids: ['S99'],
      note: 'No reference.',
    }]

    expect(() => parseInferenceResponse(response, 'tax')).toThrow(/invalid evidence reference/)
  })

  it('removes empty limitation entries without hiding actual warnings', () => {
    const response = responseFixture() as InferenceResponse & Record<string, unknown>
    response.limitations = ['', '  ', 'Check current official guidance.']

    expect(parseInferenceResponse(response, 'tax').limitations).toEqual(['Check current official guidance.'])
  })
})
