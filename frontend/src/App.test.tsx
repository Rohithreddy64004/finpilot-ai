import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Domain, InferenceResponse } from './types/api'

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

function mockFetch(response: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(response),
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
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
    expect(screen.getByText(/one question at a time using the selected domain's retrieved sources/)).toBeTruthy()
  })

  it('submits the chosen domain and renders real answer metadata and evidence', async () => {
    const fetchMock = mockFetch(answerFixture())
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: /investing/i }))
    fireEvent.click(screen.getByRole('button', { name: /what is the difference between saving and investing/i }))
    const questionInput = screen.getByRole('textbox', { name: 'Your finance question' })
    expect((questionInput as HTMLTextAreaElement).value).toBe('What is the difference between saving and investing?')
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    await expectAnswerVisible()
    expect(fetchMock).toHaveBeenCalledOnce()
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({
      domain: 'investment',
      question: 'What is the difference between saving and investing?',
    })
    expect(screen.getByText('Source passages used')).toBeTruthy()
    expect(screen.getByText('Verification unavailable')).toBeTruthy()
    expect(screen.getByText('Unverified')).toBeTruthy()
    expect(screen.getByText(/No separate verifier ran/)).toBeTruthy()
    fireEvent.click(screen.getByText(/Sources & evidence/))
    expect(screen.getByText(/Diversification can reduce the effect/)).toBeTruthy()
    expect(screen.getByText('Not a substitute for personal financial advice.')).toBeTruthy()
    expect(screen.queryByText('FinPilot AI provides general financial education, not licensed advice.')).toBeNull()
  })

  it('shows a safe backend-unavailable state', async () => {
    mockFetch({ detail: 'internal server traceback' }, 503)
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'Explain a tax rebate.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    expect((await screen.findByRole('alert')).textContent).toContain('FinPilot could not complete the request.')
    expect(screen.queryByText('internal server traceback')).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('retries the same question after a safe backend error', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        text: async () => JSON.stringify({ detail: 'internal server traceback' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(answerFixture('tax')),
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

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(JSON.parse(String(retryInit.body))).toEqual({
      domain: 'tax',
      question: 'Explain a tax rebate.',
    })
  })

  it('supports Ctrl+Enter from the question field', async () => {
    const fetchMock = mockFetch(answerFixture('tax'))
    render(<App />)
    const input = screen.getByRole('textbox', { name: 'Your finance question' })
    fireEvent.change(input, { target: { value: 'Explain investment risk.' } })
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })

    await expectAnswerVisible()
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('shows a pending state and disables duplicate submissions', async () => {
    let resolveFetch: ((response: Response) => void) | undefined
    const pendingResponse = new Promise<Response>((resolve) => { resolveFetch = resolve })
    const fetchMock = vi.fn().mockReturnValue(pendingResponse)
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'Explain risk and return.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    expect(screen.getByRole('status').textContent).toContain('Checking the selected source library')
    expect((screen.getByRole('button', { name: 'Send question' }) as HTMLButtonElement).disabled).toBe(true)
    expect(fetchMock).toHaveBeenCalledOnce()
    resolveFetch?.({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(answerFixture('tax')),
    } as Response)
    await expectAnswerVisible()
  })

  it('keeps previous conversation visible after starting a new chat', async () => {
    mockFetch(answerFixture('tax'))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Your finance question' }), {
      target: { value: 'What is a tax rebate?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))
    await expectAnswerVisible()
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))

    expect(screen.getByText('Make sense of Indian tax.')).toBeTruthy()
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
})
