import { BrainCircuit, CircleHelp, MessageSquareText, Plus } from 'lucide-react'
import type { Conversation } from '../types/chat'

interface SidebarProps {
  conversations: Conversation[]
  activeConversationId: string
  onNewChat: () => void
  onSelectConversation: (id: string) => void
  onClose?: () => void
}

function conversationTitle(conversation: Conversation): string {
  const firstQuestion = conversation.messages.find((message) => message.role === 'user')?.content
  if (!firstQuestion) return 'New conversation'
  return firstQuestion.length > 32 ? `${firstQuestion.slice(0, 32).trim()}…` : firstQuestion
}

export function Sidebar({
  conversations,
  activeConversationId,
  onNewChat,
  onSelectConversation,
  onClose,
}: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Main navigation">
      <div className="brand-row">
        <span className="brand-mark"><BrainCircuit size={21} strokeWidth={1.8} /></span>
        <div className="brand-name">FinPilot<span>AI</span><small>FINANCE, EXPLAINED</small></div>
      </div>
      <button className="new-chat-button" type="button" onClick={() => { onNewChat(); onClose?.() }}>
        <Plus size={17} /> New conversation
      </button>
      <div className="sidebar-section-label">WORKSPACE</div>
      <div className="sidebar-current" aria-current="page">
        <MessageSquareText size={16} /><span>Ask FinPilot</span><span className="nav-dot" />
      </div>
      <div className="sidebar-section-label recent-label">RECENT CHATS</div>
      <nav className="conversation-list" aria-label="Conversation history">
        {conversations.map((conversation) => (
          <button
            className={`conversation-item ${conversation.id === activeConversationId ? 'is-active' : ''}`}
            key={conversation.id}
            type="button"
            onClick={() => { onSelectConversation(conversation.id); onClose?.() }}
            aria-current={conversation.id === activeConversationId ? 'true' : undefined}
            title={conversationTitle(conversation)}
          >
            <MessageSquareText size={15} />
            <span>{conversationTitle(conversation)}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <details className="sidebar-help">
          <summary><CircleHelp size={16} /><span>About this assistant</span></summary>
          <p>FinPilot answers one question at a time using the selected domain's retrieved sources. Citations, source grounding, and claim assessments are shown separately.</p>
        </details>
        <div className="sidebar-profile"><span className="profile-initial">F</span><span>FinPilot workspace</span><span className="online-dot" aria-label="Local application" /></div>
      </div>
    </aside>
  )
}
