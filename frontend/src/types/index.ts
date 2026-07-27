export interface ChatMessage {
  id: string
  role: 'user' | 'bot'
  content: string
  tokensUsed?: number
  timestamp: number
  metadata?: Record<string, unknown>
  streaming?: boolean
}

export interface ChatRequest {
  question: string
}

export interface ChatResponse {
  answer: string
  error: boolean
  errorMessage?: string
  tokensUsed?: number
  timestamp: number
}

/** SSE frame payloads emitted by the backend (audit #23). */
export interface SseTokenPayload {
  content: string
}

export interface SseErrorPayload {
  errorMessage?: string
}

export interface ChatContextType {
  messages: ChatMessage[]
  isLoading: boolean
  isStreaming: boolean
  sendMessage: (question: string) => Promise<void>
  stopStreaming: () => void
  clearHistory: () => void
  jwtToken: string | null
  tbHost?: string | null
}
