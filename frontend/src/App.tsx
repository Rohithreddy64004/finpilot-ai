import { useMemo, useRef, useState } from 'react'
import { Menu, Plus, ShieldCheck, Sparkles, X } from 'lucide-react'
import { ChatInput } from './components/ChatInput'
import { ChatMessage } from './components/ChatMessage'
import { DomainSelector } from './components/DomainSelector'
import { Sidebar } from './components/Sidebar'
import { ApiError, requestAsk, requestFeedback } from './services/api'
import type { Domain } from './types/api'
import type { ChatEntry, Conversation } from './types/chat'
import './App.css'

const domainContent: Record<Domain, { title: string; intro: string; questions: string[] }> = {
  tax: {
    title: 'Make sense of Indian tax.',
    intro: 'Explore tax concepts with careful, source-aware explanations for India.',
    questions: [
      'What is the difference between a tax deduction and a rebate?',
      'How do income tax slabs work for individuals?',
      'What should I check before choosing a tax regime?',
    ],
  },
  investment: {
    title: 'Build investment understanding.',
    intro: 'Learn about risk, diversification and market products in plain language.',
    questions: [
      'What is the difference between saving and investing?',
      'Why does diversification matter?',
      'What should a beginner know about mutual funds?',
    ],
  },
  insurance: {
    title: 'Understand insurance with confidence.',
    intro: 'Get clear explanations of policy terms, cover and common exclusions.',
    questions: [
      'What is the difference between term and health insurance?',
      'What should I review in a policy document?',
      'What does a waiting period mean in health insurance?',
    ],
  },
}

let nextLocalId = 0
function createId(): string {
  nextLocalId += 1
  return `${Date.now()}-${nextLocalId}`
}

function createConversation(): Conversation {
  return { id: createId(), messages: [] }
}

function App() {
  const [conversations, setConversations] = useState<Conversation[]>(() => [createConversation()])
  const [activeConversationId, setActiveConversationId] = useState(() => conversations[0]?.id ?? '')
  const [selectedDomain, setSelectedDomain] = useState<Domain>('tax')
  const [selectedExperts, setSelectedExperts] = useState<Domain[]>([])
  const [draft, setDraft] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const submissionLock = useRef(false)

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeConversationId),
    [activeConversationId, conversations],
  )
  const messages = activeConversation?.messages ?? []
  const content = domainContent[selectedDomain]

  function startNewChat() {
    const currentIsEmpty = conversations.find((item) => item.id === activeConversationId)?.messages.length === 0
    if (!currentIsEmpty) {
      const conversation = createConversation()
      setConversations((current) => [conversation, ...current])
      setActiveConversationId(conversation.id)
    }
    setDraft('')
    setMobileSidebarOpen(false)
  }

  function selectConversation(id: string) {
    setActiveConversationId(id)
    setDraft('')
  }

  function updateMessage(conversationId: string, messageId: string, update: Partial<ChatEntry>) {
    setConversations((current) => current.map((conversation) =>
      conversation.id !== conversationId
        ? conversation
        : {
            ...conversation,
            messages: conversation.messages.map((message) =>
              message.id === messageId ? { ...message, ...update } : message,
            ),
          },
    ))
  }

  async function performAsk(
    conversationId: string,
    responseId: string,
    question: string,
    expertOverride: Domain[],
  ) {
    if (submissionLock.current) return
    submissionLock.current = true
    setIsSubmitting(true)
    try {
      const response = await requestAsk({
        question,
        ...(expertOverride.length > 0 ? { experts: expertOverride } : {}),
      })
      updateMessage(conversationId, responseId, {
        content: response.final_answer,
        pending: false,
        error: false,
        askResponse: response,
      })
    } catch (error) {
      const message = error instanceof ApiError
        ? error.message
        : 'FinPilot could not complete the request. Please try again.'
      updateMessage(conversationId, responseId, {
        content: message,
        pending: false,
        error: true,
        askResponse: undefined,
      })
    } finally {
      submissionLock.current = false
      setIsSubmitting(false)
    }
  }

  async function submitQuestion() {
    const question = draft.trim()
    if (!question || submissionLock.current) return

    let conversationId = activeConversationId
    let conversation = conversations.find((item) => item.id === conversationId)
    if (!conversation) {
      conversation = createConversation()
      conversationId = conversation.id
      setConversations((current) => [conversation!, ...current])
      setActiveConversationId(conversationId)
    }

    const domain = selectedDomain
    const expertOverride = [...selectedExperts]
    const userMessage: ChatEntry = {
      id: createId(),
      role: 'user',
      content: question,
      domain,
      expertOverride,
    }
    const responseId = createId()
    const pendingMessage: ChatEntry = {
      id: responseId,
      role: 'assistant',
      content: '',
      domain,
      expertOverride,
      pending: true,
    }
    setConversations((current) => current.map((item) =>
      item.id === conversationId
        ? { ...item, messages: [...item.messages, userMessage, pendingMessage] }
        : item,
    ))
    setDraft('')
    await performAsk(conversationId, responseId, question, expertOverride)
  }

  function retryQuestion(conversationId: string, responseId: string) {
    if (submissionLock.current) return
    const conversation = conversations.find((item) => item.id === conversationId)
    const responseIndex = conversation?.messages.findIndex((message) => message.id === responseId) ?? -1
    if (!conversation || responseIndex < 0) return
    const originalQuestion = [...conversation.messages.slice(0, responseIndex)]
      .reverse()
      .find((message) => message.role === 'user')
    if (!originalQuestion) return

    updateMessage(conversationId, responseId, {
      content: '',
      pending: true,
      error: false,
    })
    void performAsk(
      conversationId,
      responseId,
      originalQuestion.content,
      originalQuestion.expertOverride ?? [],
    )
  }

  function toggleExpert(expert: Domain) {
    setSelectedExperts((current) => current.includes(expert)
      ? current.filter((item) => item !== expert)
      : [...current, expert])
  }

  async function sendFeedback(requestId: string, rating: 'up' | 'down') {
    await requestFeedback(requestId, rating)
  }

  return (
    <div className="app-shell" data-theme="light">
      {mobileSidebarOpen && <button className="sidebar-scrim" type="button" aria-label="Close navigation" onClick={() => setMobileSidebarOpen(false)} />}
      <div className={`sidebar-shell ${mobileSidebarOpen ? 'mobile-open' : ''}`}>
        <Sidebar
          conversations={conversations}
          activeConversationId={activeConversationId}
          onNewChat={startNewChat}
          onSelectConversation={selectConversation}
          onClose={() => setMobileSidebarOpen(false)}
        />
      </div>

      <main className="main-shell">
        <header className="topbar">
          <button className="mobile-menu icon-button" type="button" aria-label={mobileSidebarOpen ? 'Close navigation' : 'Open navigation'} onClick={() => setMobileSidebarOpen((open) => !open)}>
            {mobileSidebarOpen ? <X size={19} /> : <Menu size={19} />}
          </button>
          <div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-slash">/</span><strong>Ask FinPilot</strong></div>
          <div className="topbar-actions">
            <span className="privacy-label"><ShieldCheck size={15} /> Credentials stay server-side</span>
            <button className="top-new-chat" type="button" onClick={startNewChat}><Plus size={16} /> <span>New chat</span></button>
          </div>
        </header>

        <div className="page-scroll">
          <section className="content-wrap">
            <div className="page-heading">
              <div>
                <div className="eyebrow"><span className="eyebrow-line" /> FINANCIAL EDUCATION, MADE CLEAR</div>
                <h1>Ask FinPilot<span className="heading-period">.</span></h1>
                <p>Thoughtful answers, grounded in your selected domain sources.</p>
              </div>
              <div className="model-note"><span className="model-note-icon"><Sparkles size={16} /></span><span><strong>AI-assisted learning</strong><small>Every answer shows its limits</small></span></div>
            </div>

            <section className="domain-panel" aria-label="Choose an expert domain">
              <div className="panel-heading"><div><span className="panel-overline">YOUR TOPIC</span><h2>Choose a domain</h2></div><span className="domain-step">01 <span>/ 02</span></span></div>
              <DomainSelector selected={selectedDomain} onSelect={setSelectedDomain} disabled={isSubmitting} />
              <fieldset className="expert-override" disabled={isSubmitting}>
                <legend>Choose experts (optional)</legend>
                <p>{selectedExperts.length
                  ? 'FinPilot will use only the selected experts.'
                  : 'Leave these unchecked to let the router choose experts.'}</p>
                <div className="expert-checkboxes">
                  {(['tax', 'investment', 'insurance'] as const).map((expert) => (
                    <label key={expert}>
                      <input
                        type="checkbox"
                        checked={selectedExperts.includes(expert)}
                        onChange={() => toggleExpert(expert)}
                      />
                      {expert === 'tax' ? 'Tax' : expert === 'investment' ? 'Investment' : 'Insurance'}
                    </label>
                  ))}
                </div>
              </fieldset>
            </section>

            <section className={`conversation-panel ${messages.length ? 'has-messages' : ''}`} aria-label="FinPilot conversation">
              {messages.length === 0 ? (
                <div className="welcome-state">
                  <div className="welcome-orbit"><span className="orbit-ring orbit-ring-one" /><span className="orbit-ring orbit-ring-two" /><span className="welcome-icon"><Sparkles size={24} /></span><i className="orbit-dot dot-one" /><i className="orbit-dot dot-two" /></div>
                  <div className="welcome-copy">
                    <span className="welcome-kicker">{content.title}</span>
                    <p>{content.intro}</p>
                  </div>
                  <div className="suggestion-grid">
                    {content.questions.map((question, index) => (
                      <button className="suggestion-card" type="button" key={question} onClick={() => setDraft(question)}>
                        <span className="suggestion-number">0{index + 1}</span><span>{question}</span><span className="suggestion-arrow">↗</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="transcript" aria-live="polite">
                  <div className="transcript-heading"><span>Conversation</span><span>{messages.filter((message) => message.role === 'user').length} question{messages.filter((message) => message.role === 'user').length === 1 ? '' : 's'}</span></div>
                  {messages.map((message) => (
                    <ChatMessage
                      entry={message}
                      key={message.id}
                      onRetry={message.role === 'assistant' && message.error
                        ? () => retryQuestion(activeConversationId, message.id)
                        : undefined}
                      retryDisabled={isSubmitting}
                      onFeedback={sendFeedback}
                    />
                  ))}
                </div>
              )}
            </section>

            <div className="composer-area">
              <ChatInput value={draft} onChange={setDraft} onSubmit={submitQuestion} disabled={isSubmitting} />
              <div className="composer-disclaimer"><ShieldCheck size={13} /> Only the current question is sent; previous turns are not included.</div>
            </div>

            <footer className="page-footer"><span>FINPILOT AI <span className="footer-dot">•</span> Educational information only</span><span>Not a substitute for personal financial advice.</span></footer>
          </section>
        </div>
      </main>
    </div>
  )
}

export default App
