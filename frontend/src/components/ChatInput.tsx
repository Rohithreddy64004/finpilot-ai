import { ArrowUp, CornerDownLeft } from 'lucide-react'
import type { FormEvent, KeyboardEvent } from 'react'

interface ChatInputProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  disabled?: boolean
}

export function ChatInput({ value, onChange, onSubmit, disabled = false }: ChatInputProps) {
  const canSubmit = value.trim().length > 0 && !disabled

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (canSubmit) onSubmit()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      if (canSubmit) onSubmit()
    }
  }

  return (
    <form className="composer" onSubmit={handleSubmit} aria-label="Ask FinPilot">
      <label className="sr-only" htmlFor="question-input">Your finance question</label>
      <textarea
        id="question-input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask a question about tax, investing, or insurance…"
        rows={2}
        maxLength={10_000}
        disabled={disabled}
      />
      <div className="composer-footer">
        <span className="composer-hint"><CornerDownLeft size={13} /> Ctrl/⌘ + Enter to send</span>
        <button className="send-button" type="submit" disabled={!canSubmit} aria-label="Send question">
          {disabled ? <span className="button-spinner" aria-hidden="true" /> : <ArrowUp size={18} strokeWidth={2.2} />}
          <span>{disabled ? 'Working' : 'Send'}</span>
        </button>
      </div>
    </form>
  )
}
