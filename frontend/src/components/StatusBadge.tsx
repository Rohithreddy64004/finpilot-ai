import type { ReactNode } from 'react'

export type BadgeTone = 'positive' | 'caution' | 'negative' | 'neutral' | 'teal'

export function StatusBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: BadgeTone }) {
  return <span className={`status-badge badge-${tone}`}>{children}</span>
}
