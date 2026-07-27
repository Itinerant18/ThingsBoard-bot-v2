import React, { useState, useRef } from 'react'
import { useChat } from '../context/ChatContext'

/** Cap the question length client-side so a huge input can't bloat the prompt/context (audit #22). */
const MAX_INPUT_LENGTH = 1000

export const ChatInput: React.FC = () => {
  const { sendMessage, isLoading, isStreaming, stopStreaming } = useChat()
  const [input, setInput] = useState('')
  const [isListening, setIsListening] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSend = async () => {
    if (input.trim() && !isLoading) {
      await sendMessage(input)
      setInput('')
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !isLoading) {
      e.preventDefault()
      handleSend()
    }
  }

  const startVoiceInput = () => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SpeechRecognition) {
      alert('Speech recognition is not supported in this browser.')
      return
    }

    if (isListening) {
      setIsListening(false)
      return
    }

    const recognition = new SpeechRecognition()
    recognition.continuous = false
    recognition.interimResults = false
    recognition.lang = 'en-US'

    recognition.onstart = () => {
      setIsListening(true)
    }

    recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript
      setInput((prev) => prev + (prev ? ' ' : '') + transcript)
    }

    recognition.onerror = (event: any) => {
      console.error('Speech recognition error:', event.error)
      setIsListening(false)
    }

    recognition.onend = () => {
      setIsListening(false)
    }

    recognition.start()
  }

  return (
    <div className="px-2 pb-2 pt-1.5 sm:px-4 sm:pb-4 sm:pt-2.5 flex-shrink-0"
      style={{
        background: 'linear-gradient(180deg, transparent 0%, rgba(244,246,249,0.9) 30%)',
      }}
    >
      <div className="max-w-3xl mx-auto w-full">
        {/* Inset Well Input */}
        <div className="inset-well flex items-center gap-2 p-1 pl-3 sm:p-1.5 sm:pl-4">
          <div className="relative flex-1">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value.slice(0, MAX_INPUT_LENGTH))}
              onKeyDown={handleKeyDown}
              placeholder="Ask anything..."
              disabled={isLoading}
              maxLength={MAX_INPUT_LENGTH}
              className="w-full bg-transparent text-sm text-[#0F172A] placeholder-slate-400 outline-none py-2.5 pr-10 pl-1 disabled:opacity-50 font-medium"
            />
            {input.length > MAX_INPUT_LENGTH - 100 && (
              <span className="absolute -top-4 right-2 text-[10px] text-slate-400">
                {input.length}/{MAX_INPUT_LENGTH}
              </span>
            )}

            {/* Microphone Icon */}
            <button
              type="button"
              onClick={startVoiceInput}
              className={`absolute right-2 top-1/2 -translate-y-1/2 transition-colors p-1.5 rounded cursor-pointer ${isListening
                ? 'text-red-600 bg-red-50 animate-pulse'
                : 'text-slate-400 hover:text-blue-600'
                }`}
              title={isListening ? "Listening... Click to stop" : "Voice Input"}
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="22" />
              </svg>
            </button>
          </div>

          {/* Stop button while streaming, otherwise Send */}
          {isStreaming ? (
            <button
              onClick={stopStreaming}
              title="Stop generating"
              className="brass-button w-10 h-10 flex items-center justify-center flex-shrink-0"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                <rect x="5" y="5" width="14" height="14" rx="2.5" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim() || isLoading}
              className="brass-button w-10 h-10 flex items-center justify-center flex-shrink-0"
            >
              <svg className="w-4 h-4 transform rotate-45 -translate-x-0.5 translate-y-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          )}
        </div>

        {/* Footer — Engraved Text */}
        {/* <div className="mt-2.5 text-[10px] text-[#78716c] text-center font-medium tracking-wide"
          style={{ textShadow: '0 1px 0 rgba(255,255,255,0.6)' }}
        >
          HMS Panel Expert · AI Powered · Diagrams supported · Ctrl+Enter to send
        </div> */}
      </div>
    </div>
  )
}
