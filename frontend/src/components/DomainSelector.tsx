import { Landmark, ShieldCheck, TrendingUp } from 'lucide-react'
import type { Domain } from '../types/api'

const domains: { id: Domain; label: string; description: string; Icon: typeof Landmark }[] = [
  { id: 'tax', label: 'Tax', description: 'Indian tax education', Icon: Landmark },
  { id: 'investment', label: 'Investing', description: 'SEBI based education', Icon: TrendingUp },
  { id: 'insurance', label: 'Insurance', description: 'Policy and coverage basics', Icon: ShieldCheck },
]

interface DomainSelectorProps {
  selected: Domain
  onSelect: (domain: Domain) => void
  disabled?: boolean
}

export function DomainSelector({ selected, onSelect, disabled = false }: DomainSelectorProps) {
  return (
    <div className="domain-selector" role="group" aria-label="Choose a finance topic">
      {domains.map(({ id, label, description, Icon }) => (
        <button
          className={`domain-option ${selected === id ? 'is-selected' : ''}`}
          key={id}
          type="button"
          aria-pressed={selected === id}
          disabled={disabled}
          onClick={() => onSelect(id)}
        >
          <span className={`domain-icon domain-${id}`}><Icon size={17} strokeWidth={1.8} /></span>
          <span className="domain-copy">
            <strong>{label}</strong>
            <small>{description}</small>
          </span>
        </button>
      ))}
    </div>
  )
}
