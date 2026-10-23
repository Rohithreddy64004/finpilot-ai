import { exampleQuestions } from './exampleQuestionList'

export function ExampleQuestions({ onSelect }: { onSelect: (question: string) => void }) {
  return (
    <div className="suggestion-grid">
      {exampleQuestions.map((question) => (
        <button
          className="suggestion-card"
          type="button"
          key={question}
          onClick={() => onSelect(question)}
        >
          {question}
        </button>
      ))}
    </div>
  )
}
