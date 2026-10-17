import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MarkdownContent } from './MarkdownContent'

describe('MarkdownContent', () => {
  it('renders headings, emphasis, lists, paragraphs, links, and evidence citations', () => {
    render(
      <>
        <MarkdownContent
          text={'## Investing\n\n**Diversification** can reduce risk, but it cannot eliminate all risk. [S1]\n\n1. First point\n2. Second point\n\n- Liquid assets\n- Long-term investments\n\n[SEBI](https://www.sebi.gov.in/)'}
        />
        <details>
          <summary>Evidence</summary>
          <article id="evidence-S1">Retrieved evidence</article>
        </details>
      </>,
    )

    expect(screen.getByRole('heading', { name: 'Investing', level: 2 })).toBeTruthy()
    expect(screen.getByText('Diversification').tagName).toBe('STRONG')
    expect(screen.getAllByRole('list')).toHaveLength(2)
    expect(screen.getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getByRole('link', { name: '[S1]' }).getAttribute('href')).toBe('#evidence-S1')
    expect(screen.getByRole('link', { name: 'SEBI' }).getAttribute('rel')).toBe('noopener noreferrer')
    fireEvent.click(screen.getByRole('link', { name: '[S1]' }))
    expect(screen.getByText('Evidence').closest('details')?.open).toBe(true)
  })

  it('renders narrowly escaped emphasis and JSON-style line breaks', () => {
    render(<MarkdownContent text={'\\*\\*Diversification\\*\\* reduces risk.\\n\\n1. First\\n2. Second'} />)

    expect(screen.getByText('Diversification').tagName).toBe('STRONG')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('preserves code and math backslashes, rejects unsafe links, and does not render raw HTML', () => {
    const { container } = render(
      <MarkdownContent
        text={'Keep `\\*\\*literal\\*\\* and \\n` and \\(x \\times y\\). [bad](javascript:alert(1))\n\n<script>window.compromised = true</script>'}
      />,
    )

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull()
    expect(screen.getByText(/literal/).tagName).toBe('CODE')
    expect(container.textContent).toContain('\\(x \\times y\\)')
  })
})
