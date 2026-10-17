import type { Plugin } from 'unified'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownNode {
  type: string
  value?: string
  url?: string
  title?: string | null
  children?: MarkdownNode[]
}

const citationMarker = /\[S([1-9]\d*)\]/g
const protectedPartPattern = /^(?:```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([\s\S]*?\\\))$/

const remarkCitationLinks: Plugin = () => (tree) => {
  const walk = (node: MarkdownNode) => {
    if (!node.children) return
    node.children = node.children.flatMap((child) => {
      if (child.type === 'text' && typeof child.value === 'string') {
        const parts: MarkdownNode[] = []
        let previousIndex = 0
        for (const match of child.value.matchAll(citationMarker)) {
          const index = match.index ?? 0
          if (index > previousIndex) {
            parts.push({ type: 'text', value: child.value.slice(previousIndex, index) })
          }
          const evidenceId = `S${match[1]}`
          parts.push({
            type: 'link',
            url: `#evidence-${evidenceId}`,
            title: null,
            children: [{ type: 'text', value: match[0] }],
          })
          previousIndex = index + match[0].length
        }
        if (previousIndex === 0) return [child]
        if (previousIndex < child.value.length) {
          parts.push({ type: 'text', value: child.value.slice(previousIndex) })
        }
        return parts
      }
      if (child.type === 'link') return [child]
      walk(child)
      return [child]
    })
  }

  walk(tree as unknown as MarkdownNode)
}

function prepareMarkdown(source: string): string {
  let prepared = source
  // Some model responses contain JSON-style line breaks as literal characters.
  // Restore them only when the whole answer uses that representation, and leave
  // code/math snippets and all unrelated backslashes unchanged.
  if (!prepared.includes('\n') && !prepared.includes('`') && !prepared.includes('$') &&
      !prepared.includes('\\(') && (prepared.match(/\\n/g)?.length ?? 0) > 0) {
    prepared = prepared.replace(/\\n/g, '\n')
  }

  const protectedPart = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([\s\S]*?\\\))/g
  return prepared.split(protectedPart).map((part) => {
    if (protectedPartPattern.test(part)) {
      return part.startsWith('\\(') && part.endsWith('\\)')
        ? part.replace(/^\\\(/, '\\\\(').replace(/\\\)$/, '\\\\)')
        : part
    }
    return part
      .replace(/\\\*\\\*([^\n]+?)\\\*\\\*/g, '**$1**')
      .replace(/\\\*([^*\n]+?)\\\*/g, '*$1*')
      .replace(/\\_\\_([^_\n]+?)\\_\\_/g, '__$1__')
      .replace(/\\_([^_\n]+?)\\_/g, '_$1_')
  }).join('')
}

const components: Components = {
  a({ href, children, node, ...props }) {
    void node
    if (href && /^#evidence-S[1-9]\d*$/.test(href)) {
      return (
        <a
          {...props}
          className="markdown-citation"
          href={href}
          onClick={() => {
            const targetId = href.slice(1)
            const target = document.getElementById(targetId)
            const details = target?.closest('details')
            if (details instanceof HTMLDetailsElement) details.open = true
          }}
        >
          {children}
        </a>
      )
    }
    if (href) {
      try {
        const parsed = new URL(href, window.location.origin)
        if ((parsed.protocol === 'https:' || parsed.protocol === 'http:') &&
            !parsed.username && !parsed.password) {
          return <a {...props} className="markdown-link" href={parsed.href} target="_blank" rel="noopener noreferrer">{children}</a>
        }
      } catch {
        // Unsafe or malformed destinations are displayed as text below.
      }
    }
    return <span>{children}</span>
  },
  img: () => null,
}

export function MarkdownContent({
  text,
  className = '',
  linkCitations = true,
}: { text: string; className?: string; linkCitations?: boolean }) {
  return (
    <div className={className}>
      <ReactMarkdown
        skipHtml
        remarkPlugins={linkCitations ? [remarkGfm, remarkCitationLinks] : [remarkGfm]}
        components={components}
      >
        {prepareMarkdown(text)}
      </ReactMarkdown>
    </div>
  )
}
