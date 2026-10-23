import { useEffect, useRef, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { ChatInput } from './components/ChatInput'
import { ChatMessage } from './components/ChatMessage'
import { exampleQuestions } from './components/exampleQuestionList'
import { Sidebar } from './components/Sidebar'
import { ApiError, requestAsk, requestFeedback } from './services/api'
import type { ChatEntry } from './types/chat'
import type { Domain } from './types/api'
import './App.css'

let nextLocalId = 0
function createId(): string {
  nextLocalId += 1
  return `${Date.now()}-${nextLocalId}`
}

function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return 'FinPilot is busy right now. Wait a moment, then try again.'
    if (error.status === 503) return 'FinPilot is temporarily unavailable. Try again shortly.'
    if (error.status === 504 || /timed? ?out|timeout/i.test(error.message)) {
      return 'This request took too long. Please try again.'
    }
    if (/cannot reach|network|fetch/i.test(error.message)) {
      return 'FinPilot could not be reached. Check your connection, then try again.'
    }
  }
  return 'FinPilot could not complete this request. Please try again.'
}

function App() {
  const [messages, setMessages] = useState<ChatEntry[]>([])
  const [selectedExperts, setSelectedExperts] = useState<Domain[]>([])
  const [draft, setDraft] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const submissionLock = useRef(false)
  const newestMessageRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isSubmitting) return undefined
    const startedAt = Date.now()
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [isSubmitting])

  useEffect(() => {
    newestMessageRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [messages, isSubmitting])

  function startNewChat() {
    if (submissionLock.current) return
    setMessages([])
    setDraft('')
  }

  function updateMessage(messageId: string, update: Partial<ChatEntry>) {
    setMessages((current) => current.map((message) =>
      message.id === messageId ? { ...message, ...update } : message,
    ))
  }

  async function performAsk(responseId: string, question: string, expertOverride: Domain[]) {
    if (submissionLock.current) return
    submissionLock.current = true
    setElapsedSeconds(0)
    setIsSubmitting(true)
    try {
      const response = await requestAsk({
        question,
        ...(expertOverride.length > 0 ? { experts: expertOverride } : {}),
      })
      updateMessage(responseId, {
        content: response.final_answer,
        pending: false,
        error: false,
        askResponse: response,
      })
    } catch (error) {
      updateMessage(responseId, {
        content: friendlyError(error),
        pending: false,
        error: true,
        askResponse: undefined,
      })
    } finally {
      submissionLock.current = false
      setIsSubmitting(false)
    }
  }

  async function submitQuestion(questionOverride?: string) {
    const question = (questionOverride ?? draft).trim()
    if (!question || submissionLock.current) return

    const expertOverride = [...selectedExperts]
    const userMessage: ChatEntry = {
      id: createId(), role: 'user', content: question, expertOverride,
    }
    const responseId = createId()
    const pendingMessage: ChatEntry = {
      id: responseId, role: 'assistant', content: '', expertOverride, pending: true,
    }
    setMessages((current) => [...current, userMessage, pendingMessage])
    setDraft('')
    await performAsk(responseId, question, expertOverride)
  }

  function retryQuestion(responseId: string) {
    if (submissionLock.current) return
    const responseIndex = messages.findIndex((message) => message.id === responseId)
    const originalQuestion = [...messages.slice(0, responseIndex)]
      .reverse()
      .find((message) => message.role === 'user')
    if (!originalQuestion) return
    updateMessage(responseId, { content: '', pending: true, error: false })
    void performAsk(responseId, originalQuestion.content, originalQuestion.expertOverride ?? [])
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
    <div className="app-shell" data-theme="light" id="top">
      <Sidebar onNewChat={startNewChat} />
      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb"><strong>Finance education</strong></div>
          <span className="privacy-label"><ShieldCheck size={15} aria-hidden="true" /> Grounded in sources</span>
        </header>

        <div className="page-scroll">
          <section className="content-wrap">
            <header className="page-heading">
              <h1>Ask FinPilot</h1>
              <p>Clear explanations grounded in relevant sources.</p>
            </header>

            <details className="advanced-options">
              <summary>Advanced</summary>
              <fieldset className="expert-override" disabled={isSubmitting}>
                <legend>Choose experts (optional)</legend>
                <p>{selectedExperts.length
                  ? 'Only the selected experts will be used.'
                  : 'Leave unchecked to let FinPilot choose.'}</p>
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
            </details>

            <section className={`conversation-panel ${messages.length ? 'has-messages' : ''}`} aria-label="FinPilot conversation">
              {messages.length === 0 ? (
                <div className="welcome-state">
                  <h2>Finance, made clearer.</h2>
                  <p>Ask a question or choose an example to get started.</p>
                  <div className="suggestion-grid">
                    {exampleQuestions.map((question) => (
                      <button
                        className="suggestion-card"
                        type="button"
                        key={question}
                        onClick={() => { setDraft(question); void submitQuestion(question) }}
                      >
                        {question}<span aria-hidden="true">→</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="transcript" aria-live="polite">
                  {messages.map((message) => (
                    <ChatMessage
                      entry={message}
                      key={message.id}
                      elapsedSeconds={message.pending ? elapsedSeconds : 0}
                      onRetry={message.role === 'assistant' && message.error
                        ? () => retryQuestion(message.id)
                        : undefined}
                      retryDisabled={isSubmitting}
                      onFeedback={sendFeedback}
                      onExampleQuestion={(question) => { void submitQuestion(question) }}
                    />
                  ))}
                  <div ref={newestMessageRef} aria-hidden="true" />
                </div>
              )}
            </section>

            <div className="composer-area">
              <ChatInput value={draft} onChange={setDraft} onSubmit={() => void submitQuestion()} disabled={isSubmitting} />
            </div>

            <footer className="page-footer">
              <span>Educational information only. Not personalized or licensed financial advice.</span>
            </footer>
          </section>
        </div>
      </main>
    </div>
  )
}

export default App
