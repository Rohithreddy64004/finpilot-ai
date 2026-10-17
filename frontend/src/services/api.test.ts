import { describe, expect, it, vi } from 'vitest'
import { ApiError, parseInferenceResponse, requestInference } from './api'
import type { Domain, InferenceResponse } from '../types/api'

function responseFixture(domain: Domain = 'tax'): InferenceResponse {
  return {
    domain,
    model_id: 'fixture-base-model',
    alignment_status: 'unaligned',
    answer: 'A deduction reduces taxable income. [S1]',
    evidence: [{
      citation_id: 'S1',
      passage_id: 'tax:document-fixture:1',
      document_id: 'document-fixture',
      document_name: 'Tax guide',
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
      'FinPilot could not complete the request. Check that the backend is running and configured, then try again.',
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
      'Cannot reach the FinPilot backend.',
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

describe('parseInferenceResponse', () => {
  it('handles absent optional display fields without failing', () => {
    const payload = {
      domain: 'tax',
      model_id: 'fixture-base-model',
      alignment_status: 'unaligned',
      answer: 'General educational answer.',
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
