import type { Domain, InferenceResponse } from './api'

export interface ChatEntry {
  id: string
  role: 'user' | 'assistant'
  content: string
  domain: Domain
  pending?: boolean
  error?: boolean
  response?: InferenceResponse
}

export interface Conversation {
  id: string
  messages: ChatEntry[]
}
