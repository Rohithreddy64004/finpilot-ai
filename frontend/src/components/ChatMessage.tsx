import { AlertCircle, Bot, LoaderCircle, UserRound } from 'lucide-react'
import type { ChatEntry } from '../types/chat'
import { AskResponsePanel } from './AskResponsePanel'

interface ChatMessageProps {
  entry: ChatEntry
  elapsedSeconds?: number
  onRetry?: () => void
  onFeedback?: (requestId: string, rating: 'up' | 'down') => Promise<void>
  retryDisabled?: boolean
}

export function ChatMessage({
  entry,
  elapsedSeconds = 0,
  onRetry,
  onFeedback,
  retryDisabled = false,
}: ChatMessageProps) {
  if (entry.role === 'user') {
    return (
      <article className="message-row user-row">
        <div className="user-message-wrap">
          <div className="message-kicker">You</div>
          <div className="user-message">{entry.content}</div>
        </div>
        <span className="avatar user-avatar" aria-hidden="true"><UserRound size={17} /></span>
      </article>
    )
  }

  return (
    <article className="message-row assistant-row" aria-label="FinPilot answer">
      <span className="avatar assistant-avatar" aria-hidden="true"><Bot size={18} /></span>
      <div className="assistant-message-wrap">
        {entry.pending ? (
          <div className="pending-answer" role="status" aria-live="polite">
            <LoaderCircle className="spin" size={17} aria-hidden="true" />
            <span>Finding sources... {elapsedSeconds} seconds</span>
            {elapsedSeconds >= 20 && <span className="slow-note">This is taking longer than usual.</span>}
          </div>
        ) : entry.error ? (
          <div className="error-panel" role="alert">
            <AlertCircle size={18} aria-hidden="true" />
            <span>{entry.content}</span>
            {onRetry && <button className="retry-button" type="button" onClick={onRetry} disabled={retryDisabled}>Try again</button>}
          </div>
        ) : entry.askResponse ? (
          <AskResponsePanel response={entry.askResponse} onFeedback={onFeedback} />
        ) : null}
      </div>
    </article>
  )
}
