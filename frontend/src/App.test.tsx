import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AskExpertResult, AskResponse, AskSource, Domain, ModelMode } from './types/api'

function sourceFixture(domain: Domain, citationId = 'S1'): AskSource {
  return {
    expert: domain,
    citation_id: citationId,
    passage_id: `${domain}:fixture:${citationId}`,
    document_id: `${domain}-document`,
    document_name: `${domain} public guide`,
    section_heading: 'General education',
    excerpt: 'Diversification can reduce the effect of one investment on a portfolio.',
    source: {
      title: `${domain} public education guide`,
      publisher: domain === 'investment' ? 'SEBI' : 'Public authority',
      url: 'https://example.gov.in/guide',
      accessed_on: '2026-10-15',
    },
  }
}

function expertFixture(
  domain: Domain,
  source: AskSource,
  alignment: 'aligned' | 'unaligned' = 'unaligned',
  mode: ModelMode = 'base_fallback',
): AskExpertResult {
  return {
    expert: domain,
    status: 'complete',
    answer: `Diversification spreads exposure across investments. [${source.citation_id}]`,
    model_id: 'fixture-base-model',
    model_mode: mode,
    alignment_status: alignment,
    answer_state: 'validated',
    answer_mode: 'generated',
    repair_retry_used: false,
    source_grounding_supported: true,
    source_grounding_note: 'Cited source text.',
    evidence: [source],
    claim_verifications: [{
      claim_text: 'Diversification spreads exposure across investments.',
      status: 'unverified',
      assessment_basis: 'source_text_comparison',
      evidence_ids: [],
      cited_evidence_ids: [source.citation_id],
      note: 'Not independently assessed.',
    }],
    claim_verification_status: 'verification_unavailable',
    independent_verification_performed: false,
    claim_verification_note: 'Verifier unavailable.',
    limitations: ['This is general education.', '  '],
    error: null,
  }
}

function askFixture(domain: Domain = 'investment'): AskResponse {
  const source = sourceFixture(domain)
  return {
    request_id: 'request-fixture',
    question: 'What is diversification?',
    routing: { experts: [domain], reason: `Selected ${domain} education.`, routing_method: 'model' },
    experts: [expertFixture(domain, source)],
    final_answer: 'Diversification spreads exposure across investments. [S1]',
    final_answer_state: 'validated',
    final_answer_mode: 'generated',
    final_excerpt_source_ids: [],
    final_answer_source_grounding_supported: true,
    final_answer_source_grounding_note: 'Every citation maps to a retrieved passage.',
    final_answer_claim_verifications: [],
    final_answer_claim_verification_status: 'verification_unavailable',
    final_answer_independent_verification_performed: false,
    final_answer_claim_verification_note: 'Verifier unavailable.',
    sources: [source],
    gaps: [],
    latency_ms: { routing_ms: 8, experts_ms: 20, per_expert_ms: { [domain]: 19 }, synthesis_ms: 0, total_ms: 31 },
  }
}

function installFetch(value: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(value),
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function sendQuestion(question = 'What is diversification?') {
  fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: 'Send question' }))
}

describe('FinPilot answer view', () => {
  it('shows one final answer by default, sources, and one base trust badge', async () => {
    const fetchMock = installFetch(askFixture())
    const { container } = render(<App />)
    sendQuestion()

    await screen.findByText('Validated')
    expect(container.querySelectorAll('.answer-copy')).toHaveLength(1)
    expect(container.querySelector('.answer-copy')?.textContent).toContain('Diversification spreads exposure')
    expect(screen.getByText('Base model')).not.toBeNull()
    expect(screen.getByRole('heading', { name: 'Sources' })).not.toBeNull()
    expect(screen.getByText('SEBI')).not.toBeNull()
    expect(screen.getByText('Retrieved 2026-10-15')).not.toBeNull()
    expect(screen.getByRole('link', { name: /Open source/ }).getAttribute('href')).toBe('https://example.gov.in/guide')
    expect(container.querySelector('.answer-copy a.markdown-citation')?.getAttribute('href')).toBe('#evidence-S1')
    expect(container.textContent).toContain('Educational information only. Not personalized or licensed financial advice.')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('keeps Details closed by default and reveals technical answer information on demand', async () => {
    const response = askFixture()
    response.gaps = ['', '   ']
    response.experts[0].limitations = ['', '  ']
    installFetch(response)
    const { container } = render(<App />)
    sendQuestion()
    await screen.findByText('Validated')

    const disclosure = screen.getByText('How this answer was made').closest('details')
    expect(disclosure?.open).toBe(false)
    expect(screen.getByText('fixture-base-model').closest('details')?.open).toBe(false)
    expect(Array.from(container.querySelectorAll('li')).every((item) => item.textContent?.trim())).toBe(true)

    fireEvent.click(screen.getByText('How this answer was made'))
    expect(disclosure?.open).toBe(true)
    expect(screen.getByText('fixture-base-model')).not.toBeNull()
    expect(screen.getByText('Not independently verified')).not.toBeNull()
    expect(screen.queryByText(/Per-claim results/)).toBeNull()
    expect(Array.from(container.querySelectorAll('li')).every((item) => item.textContent?.trim())).toBe(true)
  })

  it('deduplicates limitations and shows one concise retrieval date note', async () => {
    const response = askFixture()
    const verboseDate = 'Selected source text was retrieved on 2026-10-17, not the requested applicable date 2026-10-23; it does not independently establish that time-sensitive rules were current on that date. Check current official guidance.'
    response.gaps = [verboseDate, 'Corpus coverage is limited.', 'Corpus coverage is limited.']
    response.experts[0].limitations = [verboseDate, '  ', 'Corpus coverage is limited.']
    installFetch(response)
    const { container } = render(<App />)
    sendQuestion()
    await screen.findByText('Validated')
    fireEvent.click(screen.getByText('How this answer was made'))

    expect(screen.getByRole('heading', { name: 'Limitations and gaps' })).not.toBeNull()
    expect(screen.getByText('Sources retrieved on 2026-10-15. Check current official guidance for time-sensitive rules.')).not.toBeNull()
    expect(screen.getAllByText('Corpus coverage is limited.')).toHaveLength(1)
    expect(screen.queryByText(verboseDate)).toBeNull()
    expect(container.querySelectorAll('.details-section h3').length).toBeGreaterThan(0)
  })

  it('uses only the backend alignment status for each expert badge', async () => {
    const response = askFixture('tax')
    response.experts[0].model_mode = 'aligned'
    response.experts[0].alignment_status = 'unaligned'
    installFetch(response)
    const { container } = render(<App />)
    sendQuestion()
    await screen.findByText('Validated')
    expect(screen.getByText('Base model')).not.toBeNull()
    expect(screen.queryByText('Aligned expert')).toBeNull()

    const aligned = askFixture('tax')
    aligned.experts[0].alignment_status = 'aligned'
    installFetch(aligned)
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))
    sendQuestion('What is a tax slab?')
    await waitFor(() => expect(screen.getByText('Aligned expert')).not.toBeNull())
    expect(container.querySelectorAll('.answer-copy')).toHaveLength(1)
  })

  it('shows a short consulted line for cross-domain answers and one final answer', async () => {
    const response = askFixture('tax')
    const investmentSource = sourceFixture('investment', 'S2')
    response.routing.experts = ['tax', 'investment']
    response.routing.reason = 'This question spans tax treatment and investing.'
    response.experts.push(expertFixture('investment', investmentSource))
    response.sources.push(investmentSource)
    installFetch(response)
    const { container } = render(<App />)
    sendQuestion('Should I choose ELSS or NPS?')
    await screen.findByText('Validated')

    expect(screen.getByText('Consulted: Tax, Investment')).not.toBeNull()
    expect(container.querySelectorAll('.answer-copy')).toHaveLength(1)
    expect(screen.queryByText('How this answer was made')?.closest('details')?.open).toBe(false)
  })

  it('shows the first three sources and expands the clean source list on request', async () => {
    const response = askFixture()
    response.sources.push(
      sourceFixture('investment', 'S2'),
      sourceFixture('investment', 'S3'),
      sourceFixture('investment', 'S4'),
    )
    installFetch(response)
    const { container } = render(<App />)
    sendQuestion()
    await screen.findByText('Validated')

    expect(container.querySelectorAll('.source-card')).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: 'Show all (4)' }))
    expect(container.querySelectorAll('.source-card')).toHaveLength(4)
    fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }))
    expect(container.querySelectorAll('.source-card')).toHaveLength(3)
  })

  it.each(['http://localhost:8000/source', 'http://127.0.0.1/source', 'http://[::1]/source'])(
    'does not expose a local source URL: %s',
    async (url) => {
      const response = askFixture()
      response.sources[0].source.url = url
      installFetch(response)
      render(<App />)
      sendQuestion()
      await screen.findByText('Validated')
      expect(screen.queryByRole('link', { name: /Open source/ })).toBeNull()
      expect(document.querySelector('.answer-copy a.markdown-citation')?.getAttribute('href')).toBe('#evidence-S1')
    },
  )

  it('shows the withheld-summary state and keeps retrieved source excerpts readable', async () => {
    const response = askFixture('tax')
    response.final_answer_state = 'withheld'
    response.final_answer_mode = 'none'
    response.final_answer = 'Source excerpts (no generated summary)'
    response.final_answer_source_grounding_supported = false
    response.final_excerpt_source_ids = ['S1']
    response.sources[0].excerpt = '<b>Slab rates</b> apply by income band and tax regime.'
    response.gaps = ['Generated wording failed citation validation.']
    installFetch(response)
    const { container } = render(<App />)
    sendQuestion('How do tax slabs work?')

    expect(await screen.findByText("Sources found; summary couldn't be validated")).not.toBeNull()
    expect(screen.getByText('Source excerpts (no generated summary)')).not.toBeNull()
    expect(screen.getByText('<b>Slab rates</b> apply by income band and tax regime.')).not.toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(container.querySelectorAll('.answer-copy')).toHaveLength(0)
  })

  it('labels extractive answers as source text in the answer and expert details', async () => {
    const response = askFixture('tax')
    response.final_answer_mode = 'extractive'
    response.experts[0].answer_mode = 'extractive'
    installFetch(response)
    render(<App />)
    sendQuestion('How does a tax slab work?')

    expect(await screen.findAllByText('Extractive summary from sources')).toHaveLength(2)
    fireEvent.click(screen.getByText('How this answer was made'))
    expect(screen.getAllByText('Extractive summary from sources').length).toBeGreaterThan(1)
  })

  it('shows the no-source state without rendering an empty source list', async () => {
    const response = askFixture('tax')
    response.final_answer_state = 'no_passages'
    response.final_answer_mode = 'none'
    response.final_answer = 'No relevant source found.'
    response.final_answer_source_grounding_supported = false
    response.sources = []
    response.experts[0].evidence = []
    response.experts[0].answer_state = 'no_passages'
    response.experts[0].answer_mode = 'none'
    response.experts[0].answer = 'No relevant source found.'
    response.experts[0].source_grounding_supported = false
    response.experts[0].claim_verifications = []
    installFetch(response)
    render(<App />)
    sendQuestion('Question with no source coverage')

    expect(await screen.findByText('No relevant source found')).not.toBeNull()
    expect(screen.getByText('No relevant source was found for this question.')).not.toBeNull()
    expect(screen.queryByRole('heading', { name: 'Sources' })).toBeNull()
  })

  it('offers four example questions that submit immediately', async () => {
    const fetchMock = installFetch(askFixture())
    render(<App />)
    const examples = [
      'How do income tax slabs work for individuals?',
      'What is a SIP?',
      'What is the difference between saving and investing?',
      'Should I choose ELSS or NPS to save tax?',
    ]
    for (const question of examples) {
      expect(screen.getByRole('button', { name: question })).not.toBeNull()
    }
    fireEvent.click(screen.getByRole('button', { name: 'What is a SIP?' }))
    await screen.findByText('Validated')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ question: 'What is a SIP?' })
  })

  it('shows out-of-scope guidance and examples without source or trust details', async () => {
    const response = askFixture()
    response.routing.experts = []
    response.experts = []
    response.sources = []
    response.final_answer = 'I can help with Indian tax, investing, and insurance questions. Try asking about income tax slabs, the difference between saving and investing, or how health insurance waiting periods work.'
    response.final_answer_state = 'out_of_scope'
    response.final_answer_mode = 'none'
    response.final_answer_source_grounding_supported = false
    const fetchMock = installFetch(response)
    render(<App />)
    sendQuestion('hi')

    expect(await screen.findByText(response.final_answer)).not.toBeNull()
    expect(screen.getByRole('button', { name: 'How do income tax slabs work for individuals?' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'What is a SIP?' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'What is the difference between saving and investing?' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Should I choose ELSS or NPS to save tax?' })).not.toBeNull()
    expect(screen.queryByText('Aligned expert')).toBeNull()
    expect(screen.queryByText('Base model')).toBeNull()
    expect(screen.queryByText('Validated')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Sources' })).toBeNull()
    expect(screen.queryByText('How this answer was made')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'What is a SIP?' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ question: 'What is a SIP?' })
  })

  it('renders the backend Hi response contract as a successful conversation', async () => {
    const response = askFixture()
    response.question = 'Hi'
    response.routing = {
      experts: [],
      reason: 'Deterministic conversation handling: greeting.',
      routing_method: 'fallback',
    }
    response.experts = []
    response.final_answer = 'Hi! I can help with Indian tax, investing, and insurance. What would you like to know?'
    response.final_answer_state = 'conversational'
    response.final_answer_mode = 'none'
    response.final_excerpt_source_ids = []
    response.final_answer_source_grounding_supported = false
    response.final_answer_source_grounding_note = 'No source-grounded final answer was returned.'
    response.final_answer_claim_verifications = []
    response.final_answer_claim_verification_status = 'not_performed'
    response.final_answer_independent_verification_performed = false
    response.final_answer_claim_verification_note = 'No final claim verification has been performed.'
    response.sources = []
    response.gaps = []
    response.latency_ms = { routing_ms: 0, experts_ms: 0, per_expert_ms: {}, synthesis_ms: 0, total_ms: 0 }
    const fetchMock = installFetch(response)
    render(<App />)
    sendQuestion('Hi')

    expect(await screen.findByText(response.final_answer)).not.toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText('No relevant source was found for this question.')).toBeNull()
    expect(screen.queryByText('Aligned expert')).toBeNull()
    expect(screen.queryByText('Base model')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Sources' })).toBeNull()
    expect(screen.queryByText('How this answer was made')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['conversational', "I'm doing well, thanks for asking!"],
    ['clarification', "Could you clarify what you'd like to know?"],
  ] as const)('renders the %s response as plain conversation', async (state, answer) => {
    const response = askFixture()
    response.routing.experts = []
    response.experts = []
    response.sources = []
    response.final_answer = answer
    response.final_answer_state = state
    response.final_answer_mode = 'none'
    response.final_answer_source_grounding_supported = false
    installFetch(response)
    render(<App />)
    sendQuestion('How are you?')

    expect(await screen.findByText(answer)).not.toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText('Aligned expert')).toBeNull()
    expect(screen.queryByText('Base model')).toBeNull()
    expect(screen.queryByText('Validated')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Sources' })).toBeNull()
    expect(screen.queryByText('How this answer was made')).toBeNull()
    expect(screen.queryByText('No relevant source was found for this question.')).toBeNull()
  })

  it.each([
    [429, 'FinPilot is busy right now.'],
    [503, 'FinPilot is temporarily unavailable.'],
    [504, 'This request took too long.'],
  ])('shows a friendly retryable error for HTTP %s', async (status, message) => {
    installFetch({ detail: 'private traceback details' }, status)
    render(<App />)
    sendQuestion()
    expect((await screen.findByRole('alert')).textContent).toContain(message)
    expect(screen.getByRole('button', { name: 'Try again' })).not.toBeNull()
    expect(screen.queryByText('private traceback details')).toBeNull()
  })

  it('shows a friendly network error and allows retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network internals')))
    render(<App />)
    sendQuestion()
    expect((await screen.findByRole('alert')).textContent).toContain('FinPilot could not be reached')
    expect(screen.getByRole('button', { name: 'Try again' })).not.toBeNull()
    expect(screen.queryByText('network internals')).toBeNull()
  })

  it('shows elapsed loading time, a slow note, and disables the composer', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise(() => undefined)))
    render(<App />)
    sendQuestion()
    expect(screen.getByRole('status').textContent).toContain('Finding sources... 0 seconds')
    expect((screen.getByRole('button', { name: 'Send question' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(8_000) })
    expect(screen.getByText('The server may be waking up.')).not.toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(13_000) })
    expect(screen.getByText('This is taking longer than usual.')).not.toBeNull()
  })

  it('supports Cmd+Enter, Advanced selection, and New chat', async () => {
    const fetchMock = installFetch(askFixture('investment'))
    render(<App />)
    const advanced = screen.getByText('Advanced').closest('details')
    expect(advanced?.open).toBe(false)
    fireEvent.click(screen.getByText('Advanced'))
    fireEvent.click(screen.getByLabelText('Investment'))
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), { target: { value: 'What is a SIP?' } })
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Your finance question' }), { key: 'Enter', metaKey: true })
    await screen.findByText('Validated')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ question: 'What is a SIP?', experts: ['investment'] })
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))
    expect(screen.getByRole('heading', { name: 'Finance, made clearer.' })).not.toBeNull()
  })
})
