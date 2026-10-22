import { BrainCircuit, Plus } from 'lucide-react'

interface SidebarProps {
  onNewChat: () => void
}

export function Sidebar({ onNewChat }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="FinPilot">
      <a className="brand-row" href="#top" aria-label="FinPilot home">
        <span className="brand-mark"><BrainCircuit size={21} strokeWidth={1.8} aria-hidden="true" /></span>
        <span className="brand-name">FinPilot</span>
      </a>
      <button className="new-chat-button" type="button" onClick={onNewChat}>
        <Plus size={17} aria-hidden="true" /> New chat
      </button>
    </aside>
  )
}
