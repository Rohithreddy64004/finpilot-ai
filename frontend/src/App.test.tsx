import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AskResponse, Domain, InferenceResponse } from './types/api'

function answerFixture(domain: Domain = 'investment'): InferenceResponse {
  return {
    domain,
    model_id: 'fixture-base-model',
    alignment_status: 'unaligned',
    answer: 'Diversification spreads exposure across investments. [S1]',
    evidence: [{
      citation_id: 'S1',
      passage_id: 'investment:fixture:1',
      document_id: 'document-fixture',
      document_name: 'SEBI investor guide',
      excerpt: 'Diversification can reduce the effect of one investment on a portfolio.',
      source: {
        title: 'Investor education guide',
        publisher: 'SEBI',
        url: 'https://www.sebi.gov.in/investor',
        accessed_on: '2026-10-15',
      },
    }],
    source_references: [],
    source_grounding_supported: true,
    source_grounding_note: 'The answer cites a retrieved passage.',
    claim_verifications: [{
      claim_text: 'Diversification spreads exposure across investments.',
      status: 'unverified',
      assessment_basis: 'source_text_comparison',
      evidence_ids: [],
      cited_evidence_ids: ['S1'],
      note: 'No direct wording match was found in this claim\'s cited passage(s).',
    }],
    claim_verification_status: 'verification_unavailable',
    independent_verification_performed: false,
    claim_verification_note: 'No independent claim verifier is configured.',
    limitations: ['This is general education.'],
    applicable_as_of: '2026-10-16',
    educational_disclaimer: 'FinPilot AI provides general financial education, not licensed advice.',
  }
}

function askFixture(domain: Domain = 'investment'): AskResponse {
  const source = {
    expert: domain,
    citation_id: 'S1',
    passage_id: domain + ':fixture:1',
    document_id: 'document-fixture',
    document_name: domain + ' public guide',
    excerpt: 'Diversification can reduce the effect of one investment on a portfolio.',
    source: {
      title: 'Public education guide',
      publisher: domain === 'investment' ? 'SEBI' : 'Public authority',
      url: 'https://example.gov.in/guide',
      accessed_on: '2026-10-15',
    },
  }
  return {
    request_id: 'request-fixture',
    question: 'What is diversification?',
    routing: {
      experts: [domain],
      reason: 'Selected ' + domain + ' education.',
      routing_method: 'fallback',
    },
    experts: [{
      expert: domain,
      status: 'complete',
      answer: 'Diversification spreads exposure across investments. [S1]',
      model_id: 'fixture-base-model',
      alignment_status: 'unaligned',
      source_grounding_supported: true,
      source_grounding_note: 'The answer cites a retrieved passage.',
      evidence: [source],
      claim_verifications: [],
      claim_verification_status: 'verification_unavailable',
      independent_verification_performed: false,
      claim_verification_note: 'No independent claim verifier is configured.',
      limitations: ['This is general education.'],
      error: null,
    }],
    final_answer: 'Diversification spreads exposure across investments. [S1]',
    final_answer_source_grounding_supported: true,
    final_answer_source_grounding_note: 'Every citation maps to a retrieved passage.',
    final_answer_claim_verifications: [],
    final_answer_claim_verification_status: 'verification_unavailable',
    final_answer_independent_verification_performed: false,
    final_answer_claim_verification_note: 'No independent claim verifier is configured.',
    sources: [source],
    gaps: [],
    latency_ms: {
      routing_ms: 8,
      experts_ms: 20,
      per_expert_ms: { [domain]: 19 },
      synthesis_ms: 0,
      total_ms: 31,
    },
  }
}

function mockFetch(response: unknown, status = 200, sourceHealth?: unknown) {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const isHealth = String(input) === '/api/source-health'
    const value = isHealth ? sourceHealth ?? {
      domains: ['tax', 'investment', 'insurance'].map((domain) => ({
        domain,
        active_document_ids: [`${domain}-fixture`],
        status: 'ready',
        message: 'The active source passed local quality checks.',
        issues: [],
      })),
    } : response
    const responseStatus = isHealth ? 200 : status
    return Promise.resolve({
      ok: responseStatus >= 200 && responseStatus < 300,
      status: responseStatus,
      text: async () => JSON.stringify(value),
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function askCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([input]) => String(input) !== '/api/source-health')
}

async function expectAnswerVisible() {
  const matches = await screen.findAllByText(/Diversification spreads exposure across investments/)
  expect(matches.some((element) => element.closest('.answer-copy'))).toBe(true)
}

describe('FinPilot chat interface', () => {
  it('starts in the light theme and exposes an accessible assistant disclosure', () => {
    const { container } = render(<App />)

    expect(container.querySelector('.app-shell')?.getAttribute('data-theme')).toBe('light')
    fireEvent.click(screen.getByText('About this assistant'))
    expect(screen.getByText(/routes one question at a time across tax, investment, and insurance/)).toBeTruthy()
  })

  it('lets the router choose and renders answer metadata and source evidence', async () => {
    const fetchMock = mockFetch(askFixture())
    const { container } = render(<App />)

    expect(container.querySelector('.domain-selector')).toBeNull()
    expect(screen.getByLabelText('Investment')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /what is the difference between saving and investing/i }))
    const questionInput = screen.getByRole('textbox', { name: 'Your finance question' })
    expect((questionInput as HTMLTextAreaElement).value).toBe('What is the difference between saving and investing?')
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    await expectAnswerVisible()
    expect(askCalls(fetchMock)).toHaveLength(1)
    const [, init] = askCalls(fetchMock)[0] as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({
      question: 'What is the difference between saving and investing?',
    })
    expect(screen.getAllByText('Source passages used').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Verification unavailable')).toHaveLength(2)
    expect(screen.getByText(/No independent claim verifier is configured/)).toBeTruthy()
    expect(screen.getByText('Base model (not aligned)')).toBeTruthy()
    expect(screen.getByText('fixture-base-model')).toBeTruthy()
    fireEvent.click(screen.getByText(/Sources & evidence/))
    expect(screen.getByText(/Diversification can reduce the effect/)).toBeTruthy()
    expect(screen.getAllByText(/SEBI/).length).toBeGreaterThan(0)
    expect(screen.getByText('Retrieved 2026-10-15')).toBeTruthy()
    const sebiSourceLink = screen.getAllByRole('link').find(
      (link) => link.getAttribute('href') === 'https://example.gov.in/guide',
    )
    expect(sebiSourceLink).toBeTruthy()
    expect(screen.getByText('Not a substitute for personal financial advice.')).toBeTruthy()
    expect(screen.queryByText('FinPilot AI provides general financial education, not licensed advice.')).toBeNull()
  }, 10_000)

  it('sends an explicit expert override only when one is selected', async () => {
    const fetchMock = mockFetch(askFixture('insurance'))
    render(<App />)
    fireEvent.click(screen.getByLabelText('Insurance'))
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'What is an insurance premium?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    await expectAnswerVisible()
    const [, init] = askCalls(fetchMock)[0] as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({
      question: 'What is an insurance premium?',
      experts: ['insurance'],
    })
  })

  it('shows a safe backend-unavailable state', async () => {
    mockFetch({ detail: 'internal server traceback' }, 503)
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'Explain a tax rebate.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    expect((await screen.findByRole('alert')).textContent).toContain('FinPilot service is not ready')
    expect(screen.queryByText('internal server traceback')).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('shows actionable source banners for invalid and missing active corpora', async () => {
    mockFetch(askFixture(), 200, {
      domains: [
        {
          domain: 'tax', active_document_ids: ['old-tax-doc'], status: 'invalid',
          message: 'The active tax source fails local quality checks. Review the corpus, then register and activate a verified source before retrying.',
          issues: ['instruction-style heading'],
        },
        {
          domain: 'investment', active_document_ids: [], status: 'missing',
          message: 'No active investment source is selected. Register a verified official corpus and activate it before asking this expert.',
          issues: [],
        },
        {
          domain: 'insurance', active_document_ids: ['insurance-doc'], status: 'ready',
          message: 'The active insurance source passed local quality checks.', issues: [],
        },
      ],
    })
    render(<App />)

    expect(await screen.findByText(/Tax source needs attention/)).toBeTruthy()
    expect(screen.getByText(/register and activate a verified source before retrying/)).toBeTruthy()
    expect(screen.getByText(/Investment source needs attention/)).toBeTruthy()
    expect(screen.queryByText(/Insurance source needs attention/)).toBeNull()
  })

  it('retries the same question after a safe backend error', async () => {
    const askResponses = [
      { value: { detail: 'internal server traceback' }, status: 503 },
      { value: askFixture('tax'), status: 200 },
    ]
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (String(input) === '/api/source-health') {
        return Promise.resolve({
          ok: true,
          status: 200,
          text: async () => JSON.stringify({
            domains: ['tax', 'investment', 'insurance'].map((domain) => ({
              domain, active_document_ids: [`${domain}-fixture`], status: 'ready',
              message: 'Ready.', issues: [],
            })),
          }),
        })
      }
      const next = askResponses.shift()!
      return Promise.resolve({
        ok: next.status >= 200 && next.status < 300,
        status: next.status,
        text: async () => JSON.stringify(next.value),
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'Explain a tax rebate.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))
    await screen.findByRole('button', { name: 'Retry' })

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await expectAnswerVisible()

    expect(askCalls(fetchMock)).toHaveLength(2)
    const [, retryInit] = askCalls(fetchMock)[1] as [string, RequestInit]
    expect(JSON.parse(String(retryInit.body))).toEqual({ question: 'Explain a tax rebate.' })
  })

  it('supports Ctrl+Enter from the question field', async () => {
    const fetchMock = mockFetch(askFixture('tax'))
    render(<App />)
    const input = screen.getByRole('textbox', { name: 'Your finance question' })
    fireEvent.change(input, { target: { value: 'Explain investment risk.' } })
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })

    await expectAnswerVisible()
    expect(askCalls(fetchMock)).toHaveLength(1)
  })

  it('shows a pending state and disables duplicate submissions', async () => {
    let resolveFetch: ((response: Response) => void) | undefined
    const pendingResponse = new Promise<Response>((resolve) => { resolveFetch = resolve })
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (String(input) !== '/api/source-health') return pendingResponse
      return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          domains: ['tax', 'investment', 'insurance'].map((domain) => ({
            domain, active_document_ids: [`${domain}-fixture`], status: 'ready',
            message: 'Ready.', issues: [],
          })),
        }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'Explain risk and return.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    expect(screen.getByRole('status').textContent).toContain('Routing and checking')
    expect((screen.getByRole('button', { name: 'Send question' }) as HTMLButtonElement).disabled).toBe(true)
    expect(askCalls(fetchMock)).toHaveLength(1)
    resolveFetch?.({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(askFixture('tax')),
    } as Response)
    await expectAnswerVisible()
  })

  it('keeps previous conversation visible after starting a new chat', async () => {
    mockFetch(askFixture('tax'))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'What is a tax rebate?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))
    await expectAnswerVisible()
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))

    expect(screen.getByText('Make sense of personal finance.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /what is a tax rebate/i }))
    expect(screen.getAllByText(/Diversification spreads exposure across investments/)
      .some((element) => element.closest('.answer-copy'))).toBe(true)
  })

  it('renders safe answer Markdown and opens the matching cited evidence', async () => {
    const { ChatMessage } = await import('./components/ChatMessage')
    const response = answerFixture()
    response.answer = '## Investing\n\n**Diversification** can reduce risk. [S1]\n\n1. Spread exposure\n2. Keep risk in view'
    response.claim_verifications = [{
      claim_text: 'Diversification can reduce risk.',
      status: 'supported',
      assessment_basis: 'source_text_comparison',
      evidence_ids: ['S1'],
      cited_evidence_ids: ['S1'],
      note: 'Claim wording directly matches text in its cited passage.',
    }]
    response.claim_verification_status = 'evidence_supported'

    render(<ChatMessage entry={{
      id: 'assistant-markdown', role: 'assistant', content: response.answer, domain: 'investment', response,
    }} />)

    expect(screen.getByRole('heading', { name: 'Answer' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Claim verification' })).toBeTruthy()
    expect(screen.getByText('Diversification').tagName).toBe('STRONG')
    expect(document.querySelectorAll('.answer-copy li')).toHaveLength(2)
    const citation = screen.getAllByRole('link', { name: '[S1]' })[0]
    expect(citation.getAttribute('href')).toBe('#evidence-S1')
    fireEvent.click(citation)
    expect(screen.getByText(/Sources & evidence \(1\)/).closest('details')?.open).toBe(true)
  })

  it('orders source cards by citation ID and keeps each URL tied to its passage', async () => {
    const { EvidencePanel } = await import('./components/EvidencePanel')
    const base = answerFixture().evidence?.[0]
    if (!base) throw new Error('Fixture evidence is required')
    const second = {
      ...base,
      citation_id: 'S2',
      passage_id: 'investment:fixture:2',
      document_id: 'document-second',
      document_name: 'Second SEBI guide',
      source: {
        title: 'Second source title',
        publisher: 'SEBI Investor',
        url: 'https://investor.sebi.gov.in/second-source',
        accessed_on: '2026-10-17',
      },
    }
    const first = {
      ...base,
      citation_id: 'S1',
      passage_id: 'investment:fixture:1',
      document_name: 'First SEBI guide',
      source: {
        title: 'First source title',
        publisher: 'SEBI Investor',
        url: 'https://investor.sebi.gov.in/first-source',
        accessed_on: '2026-10-16',
      },
    }
    const { container } = render(<EvidencePanel evidence={[second, first]} />)
    fireEvent.click(screen.getByText(/Sources & evidence \(2\)/))

    const cards = Array.from(container.querySelectorAll('.evidence-item'))
    expect(cards.map((card) => card.id)).toEqual(['evidence-S1', 'evidence-S2'])
    expect(cards[0].textContent).toContain('First source title')
    expect(cards[1].querySelector('a')?.getAttribute('href')).toBe(
      'https://investor.sebi.gov.in/second-source',
    )
    expect(cards[0].textContent).toContain('Retrieved 2026-10-16')
  })

  it('shows a source-date warning beside the answer instead of hiding it in collapsed limitations', async () => {
    const { ChatMessage } = await import('./components/ChatMessage')
    const response = answerFixture('tax')
    response.limitations = [
      'Selected source text was retrieved on 2020-01-01, not the requested applicable date 2026-10-15; check current official guidance.',
    ]

    render(<ChatMessage entry={{
      id: 'assistant-stale-source', role: 'assistant', content: response.answer, domain: 'tax', response,
    }} />)

    const warning = screen.getByRole('status')
    expect(warning.textContent).toContain('2020-01-01')
    expect(warning.closest('.limitations-details')).toBeNull()
    expect(screen.getByText(/Sources & evidence/)).toBeTruthy()
  })

  it('hides empty optional sections and does not render model HTML or unsafe links', async () => {
    const { ChatMessage } = await import('./components/ChatMessage')
    const response = answerFixture()
    response.answer = '<script>window.compromised = true</script>\n\n[unsafe](javascript:alert(1))'
    response.limitations = ['', '   ']
    if (response.evidence?.[0]) response.evidence[0].source.url = 'javascript:alert(1)'
    response.claim_verifications = undefined
    response.claim_verification_status = undefined
    response.claim_verification_note = undefined

    const { container } = render(<ChatMessage entry={{
      id: 'assistant-safe', role: 'assistant', content: response.answer, domain: 'investment', response,
    }} />)

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull()
    expect(container.querySelector('.limitations-details')).toBeNull()
    expect(container.querySelector('.source-link')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Claim verification' })).toBeNull()
  })

  it('labels unavailable source grounding without implying evidence was used', async () => {
    const { ChatMessage } = await import('./components/ChatMessage')
    const response = answerFixture('tax')
    response.answer = 'The active source does not contain the requested rates.'
    response.evidence = []
    response.source_grounding_supported = false
    response.source_grounding_note = 'No relevant local passage matched the question.'

    render(<ChatMessage entry={{
      id: 'assistant-no-evidence', role: 'assistant', content: response.answer, response,
    }} />)

    expect(screen.getByText('Source grounding unavailable')).toBeTruthy()
    expect(screen.getByText('No relevant local passage matched the question.')).toBeTruthy()
    expect(screen.queryByText('Sources & evidence (0)')).toBeNull()
  })

  it('shows source-content and timeout errors clearly for expert results', async () => {
    const { AskResponsePanel } = await import('./components/AskResponsePanel')
    const response = askFixture('insurance')
    response.experts[0].status = 'error'
    response.experts[0].error = 'Source content unavailable: the insurance text is missing.'
    const view = render(<AskResponsePanel response={response} />)
    expect(screen.getByRole('status').textContent).toBe(
      'Source content unavailable: the insurance text is missing.',
    )

    view.rerender(<AskResponsePanel response={{
      ...response,
      experts: [{ ...response.experts[0], error: 'The request timed out after 45 seconds.' }],
    }} />)
    expect(screen.getByRole('status').textContent).toBe(
      'Expert timed out. The request timed out after 45 seconds.',
    )
  })
})
