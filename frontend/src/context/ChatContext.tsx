import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react'
import { ChatMessage, ChatResponse, ChatContextType, SseTokenPayload, SseErrorPayload } from '../types'

const ChatContext = createContext<ChatContextType | null>(null)

export const ChatProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const [jwtToken, setJwtToken] = useState<string | null>(null)
  const [tbHost, setTbHost] = useState<string | null>(null)

  // Controller for the in-flight stream so the user can cancel mid-generation.
  const abortRef = useRef<AbortController | null>(null)

  // Get JWT token and TB Host from various sources
  useEffect(() => {
    const token = getJwtToken()
    if (token) setJwtToken(token)

    const host = getTbHost()
    if (host) setTbHost(host)

    // Listen for postMessage from ThingsBoard.
    // SECURITY: only trust messages from a known ThingsBoard origin. Without this, any site
    // embedding this widget (or any frame on the page) could inject a forged JWT/host.
    const handleMessage = (event: MessageEvent) => {
      if (!isTrustedOrigin(event.origin)) {
        return
      }
      // Keep the token in memory (React state) only — do NOT persist to localStorage.
      if (event.data?.type === 'TB_AUTH_TOKEN' && event.data?.token) {
        setJwtToken(event.data.token)
      }
      if (event.data?.type === 'TB_HOST' && event.data?.host) {
        setTbHost(event.data.host)
      }
      if (event.data?.type === 'TB_AUTH_DATA' && event.data?.token) {
        setJwtToken(event.data.token)
        if (event.data.host) {
          setTbHost(event.data.host)
        }
      }
    }

    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [])

  const sendMessage = useCallback(
    async (question: string) => {
      if (!question.trim() || isLoading) return

      const userMessage: ChatMessage = {
        id: Date.now().toString(),
        role: 'user',
        content: question,
        timestamp: Date.now()
      }

      setMessages(prev => [...prev, userMessage])
      setIsLoading(true)
      setIsStreaming(true)

      const controller = new AbortController()
      abortRef.current = controller

      const botId = (Date.now() + 1).toString()
      // True until the first token arrives; while true the typing indicator
      // shows. Once the bot bubble exists we stream into it instead.
      let botCreated = false

      // Lazily insert the streaming bot bubble on the first token (or on done
      // for deterministic answers), then append/replace its content.
      const ensureBotMessage = () => {
        if (botCreated) return
        botCreated = true
        setIsLoading(false)
        setMessages(prev => [
          ...prev,
          { id: botId, role: 'bot', content: '', timestamp: Date.now(), streaming: true }
        ])
      }

      // Batch incoming tokens into one state update per animation frame.
      // OpenAI can emit dozens of chunks per second; committing each one
      // separately causes choppy re-renders. Coalescing per frame keeps the
      // stream smooth and the scroll fluid.
      let pending = ''
      let frameScheduled = false
      const flushPending = () => {
        frameScheduled = false
        if (!pending) return
        const chunk = pending
        pending = ''
        setMessages(prev =>
          prev.map(m => (m.id === botId ? { ...m, content: m.content + chunk } : m))
        )
      }

      const appendToken = (chunk: string) => {
        ensureBotMessage()
        pending += chunk
        if (!frameScheduled) {
          frameScheduled = true
          requestAnimationFrame(flushPending)
        }
      }

      const finalizeMessage = (data: ChatResponse) => {
        ensureBotMessage()
        // Drop any unflushed tokens; `done` carries the full canonical answer,
        // so a late animation-frame flush must not append a stale tail.
        pending = ''
        setMessages(prev =>
          prev.map(m =>
            m.id === botId
              ? {
                  ...m,
                  content: data.error
                    ? (data.errorMessage || data.answer || 'Something went wrong')
                    : data.answer,
                  tokensUsed: data.tokensUsed,
                  timestamp: data.timestamp || Date.now(),
                  streaming: false
                }
              : m
          )
        )
      }

      try {
        const headers: Record<string, string> = {
          'Content-Type': 'application/json'
        }

        if (jwtToken) {
          headers['X-TB-Token'] = jwtToken.trim()
        }
        if (tbHost) {
          headers['X-TB-Host'] = tbHost.trim()
        }

        // v2 mounts the chat router without a prefix, so the SSE route is /ask/stream
        // (not the Java app's /api/v1/chat/ask/stream), and its body field is `message`.
        // The backend replies with a single `done` frame — see app/api/chat.py.
        const response = await fetch('/ask/stream', {
          method: 'POST',
          headers,
          body: JSON.stringify({ message: question, conversation_id: 'default' }),
          signal: controller.signal
        })

        if (!response.ok || !response.body) {
          throw new Error(`Stream request failed: ${response.status}`)
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        // SSE frames are separated by a blank line. Each frame has an
        // "event:" line and one or more "data:" lines.
        const processFrame = (frame: string) => {
          let eventName = 'message'
          const dataLines: string[] = []
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) {
              eventName = line.slice('event:'.length).trim()
            } else if (line.startsWith('data:')) {
              dataLines.push(line.slice('data:'.length).trimStart())
            }
          }
          if (dataLines.length === 0) return

          let payload: unknown
          try {
            payload = JSON.parse(dataLines.join('\n'))
          } catch {
            return
          }

          if (eventName === 'token') {
            const { content } = payload as SseTokenPayload
            if (typeof content === 'string') appendToken(content)
          } else if (eventName === 'done') {
            finalizeMessage(payload as ChatResponse)
          } else if (eventName === 'error') {
            const { errorMessage } = payload as SseErrorPayload
            finalizeMessage({
              answer: '',
              error: true,
              errorMessage: errorMessage || 'Something went wrong',
              timestamp: Date.now()
            })
          }
        }

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          let sepIndex
          while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, sepIndex)
            buffer = buffer.slice(sepIndex + 2)
            if (frame.trim()) processFrame(frame)
          }
        }

        // Flush any trailing frame without a terminating blank line.
        if (buffer.trim()) processFrame(buffer)
      } catch (error) {
        if (controller.signal.aborted) {
          // User cancelled: keep whatever streamed so far, drop the cursor.
          if (botCreated) {
            const tail = pending
            pending = ''
            setMessages(prev =>
              prev.map(m => (m.id === botId ? { ...m, content: m.content + tail, streaming: false } : m))
            )
          }
        } else {
          console.error('Chat error:', error)
          finalizeMessage({
            answer: '',
            error: true,
            errorMessage: 'Unable to reach the server. Please try again.',
            timestamp: Date.now()
          })
        }
      } finally {
        setIsLoading(false)
        setIsStreaming(false)
        abortRef.current = null
      }
    },
    [isLoading, jwtToken, tbHost]
  )

  // Cancel the in-flight stream (Stop button). The fetch reader rejects with
  // AbortError, which the sendMessage catch handles by keeping partial text;
  // the server-side OpenAI read also aborts when the socket closes.
  const stopStreaming = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const clearHistory = useCallback(() => {
    setMessages([])
  }, [])

  return (
    <ChatContext.Provider
      value={{ messages, isLoading, isStreaming, sendMessage, stopStreaming, clearHistory, jwtToken, tbHost }}
    >
      {children}
    </ChatContext.Provider>
  )
}

export const useChat = () => {
  const context = useContext(ChatContext)
  if (!context) {
    throw new Error('useChat must be used within ChatProvider')
  }
  return context
}

// Origins permitted to deliver the ThingsBoard auth token via postMessage. Configurable at
// build time via VITE_TB_ALLOWED_ORIGINS (comma-separated); always includes the app's own
// origin so same-origin embedding works without configuration.
function allowedOrigins(): string[] {
  const configured = (import.meta.env.VITE_TB_ALLOWED_ORIGINS as string | undefined) || ''
  const fromEnv = configured.split(',').map(o => o.trim()).filter(Boolean)
  const defaults = ['https://app.swatch360.seple.in']
  return Array.from(new Set([window.location.origin, ...defaults, ...fromEnv]))
}

function isTrustedOrigin(origin: string): boolean {
  if (!origin) return false
  return allowedOrigins().includes(origin)
}

function getJwtToken(): string | null {
  // The token arrives via origin-checked postMessage (preferred). As a same-origin bootstrap for
  // iframe embedding we may read it once from the parent window. It is intentionally NOT read from
  // URL params (leaks into history/logs/referrer).
  try {
    if (window.parent !== window) {
      const parentToken = window.parent.localStorage?.getItem('jwt_token') || window.parent.localStorage?.getItem('tb_token')
      if (parentToken) return parentToken
    }
  } catch (e) {
    console.debug('Cannot access parent window', e)
  }

  // Standalone fallback: when the widget is opened directly (not embedded in ThingsBoard), read the
  // token from this origin's own localStorage so it works outside the iframe. The token is advisory
  // only — ThingsBoard re-validates it on every privileged call — so this does not grant access on
  // its own.
  try {
    // tb_jwt is the key the v2 single-file tester used (now at /ui/tester.html); keep
    // reading it so a token already set in a browser keeps working after this upgrade.
    const ownToken =
      window.localStorage.getItem('jwt_token') ||
      window.localStorage.getItem('tb_token') ||
      window.localStorage.getItem('tb_jwt')
    if (ownToken) return ownToken
  } catch (e) {
    console.debug('Cannot access localStorage', e)
  }

  return null
}

function getTbHost(): string | null {
  // Same-origin parent bootstrap only; not read from URL params or localStorage.
  try {
    if (window.parent !== window) {
      const parentHost = window.parent.location.origin
      if (parentHost) return parentHost
    }
  } catch (e) {
    console.debug('Cannot access parent window origin', e)
  }

  return null
}
