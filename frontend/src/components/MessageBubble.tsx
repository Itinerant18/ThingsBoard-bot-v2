import React from 'react'
import { ChatMessage } from '../types'
import botLogoUrl from '../assets/icons8-chatbot-96.apng.png'

const renderFormattedText = (text: string) => {
  if (!text) return null
  const parts = text.split('**')
  return parts.map((part, idx) => {
    if (idx % 2 === 1) {
      return <strong key={idx} className="font-extrabold text-[#0F172A]">{part}</strong>
    }
    return part
  })
}

interface MessageBubbleProps {
  message: ChatMessage
  onSuggestionClick?: (question: string) => void
}

const CollapsibleSection: React.FC<{ summary: string; content: string; isOpenDefault: boolean }> = ({
  summary,
  content,
  isOpenDefault
}) => {
  const [isOpen, setIsOpen] = React.useState(isOpenDefault)

  return (
    <div className="my-2 border border-slate-200 rounded-lg overflow-hidden bg-slate-50/50">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-3 py-2 bg-slate-100 hover:bg-blue-50 text-slate-900 font-bold text-xs transition-colors border-b border-slate-200 cursor-pointer"
      >
        <span className="flex items-center gap-1.5 text-left">
          <span className="text-[#2563EB] text-[9px]">{isOpen ? '▼' : '▶'}</span>
          {summary}
        </span>
        <span className="text-[9px] text-[#2563EB] font-bold flex-shrink-0 ml-2">{isOpen ? 'Hide' : 'Show'}</span>
      </button>
      {isOpen && (
        <div className="p-3 text-xs whitespace-pre-wrap break-words leading-relaxed text-slate-700 bg-white">
          {renderFormattedText(content)}
        </div>
      )}
    </div>
  )
}

interface Block {
  type: 'text' | 'details'
  content: string
  summary?: string
  isOpenDefault?: boolean
}

const parseContentBlocks = (text: string): Block[] => {
  const blocks: Block[] = []
  if (!text) return blocks

  const detailsRegex = /<details( open)?>\s*<summary>(.*?)<\/summary>(.*?)\s*<\/details>/gs
  let lastIndex = 0
  let match

  while ((match = detailsRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      blocks.push({
        type: 'text',
        content: text.substring(lastIndex, match.index)
      })
    }

    const isOpenDefault = !!match[1]
    const summaryHtml = match[2]
    const summary = summaryHtml.replace(/<[^>]*>/g, '').trim()
    const content = match[3].trim()

    blocks.push({
      type: 'details',
      summary,
      content,
      isOpenDefault
    })

    lastIndex = detailsRegex.lastIndex
  }

  if (lastIndex < text.length) {
    blocks.push({
      type: 'text',
      content: text.substring(lastIndex)
    })
  }

  return blocks
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message, onSuggestionClick }) => {
  const isUser = message.role === 'user'
  const [isCopied, setIsCopied] = React.useState(false)

  const handleCopy = async () => {
    const textToCopy = message.content || ''
    if (!textToCopy) return

    const fallbackCopy = (text: string) => {
      const textArea = document.createElement('textarea')
      textArea.value = text
      textArea.style.top = '0'
      textArea.style.left = '0'
      textArea.style.position = 'fixed'
      document.body.appendChild(textArea)
      textArea.focus()
      textArea.select()
      try {
        const successful = document.execCommand('copy')
        if (successful) {
          setIsCopied(true)
          setTimeout(() => setIsCopied(false), 2000)
        } else {
          console.error('Fallback copy was unsuccessful')
        }
      } catch (err) {
        console.error('Fallback copy failed', err)
      }
      document.body.removeChild(textArea)
    }

    if (!navigator.clipboard) {
      fallbackCopy(textToCopy)
      return
    }

    try {
      await navigator.clipboard.writeText(textToCopy)
      setIsCopied(true)
      setTimeout(() => setIsCopied(false), 2000)
    } catch (err) {
      console.error('Clipboard API failed: ', err)
      fallbackCopy(textToCopy)
    }
  }

  let mainContent = message.content || ''
  let suggestions: string[] = []

  if (!isUser && mainContent.includes('[SUGGESTIONS]')) {
    const parts = mainContent.split('[SUGGESTIONS]')
    mainContent = parts[0].trim()
    const suggestionsText = parts[1] || ''

    suggestions = suggestionsText
      .split('\n')
      .map(line => line.trim())
      .filter(line => line.startsWith('-') || line.startsWith('*'))
      .map(line => line.substring(1).trim().replace(/^\*|\*$/g, '').trim())
      .filter(line => line.length > 0)
  }

  const blocks = parseContentBlocks(mainContent)

  return (
    <div className={`flex gap-2 sm:gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'} message-enter items-start w-full max-w-[97%] sm:max-w-[88%] ${isUser ? 'ml-auto' : 'mr-auto'}`}>
      {/* Avatar */}
      <div className={`w-7 h-7 sm:w-8 sm:h-8 rounded-lg flex items-center justify-center flex-shrink-0 shadow-sm overflow-hidden ${
        isUser 
          ? 'bg-[#1E293B] text-white font-bold text-xs border border-slate-700' 
          : 'bg-gradient-to-br from-blue-600 to-blue-700 text-white font-bold text-xs border border-blue-500/30'
      }`}>
        {isUser ? 'U' : <img src={botLogoUrl} alt="Bot" className="w-5 h-5 sm:w-6 sm:h-6 object-contain" />}
      </div>

      {/* Bubble Wrapper */}
      <div className={`flex flex-col gap-1.5 min-w-0 flex-1 max-w-[calc(100%-2.5rem)] ${isUser ? 'items-end' : 'items-start'}`}>
        
        {/* Bubble */}
        <div
          className={`px-3.5 py-2.5 sm:px-4 sm:py-3 text-xs sm:text-sm leading-relaxed ${
            isUser
              ? 'leather-bubble rounded-tr-none'
              : 'paper-card rounded-tl-none text-[#0F172A]'
          }`}
          style={!isUser ? { transform: 'none' } : undefined}
        >
        <div className="space-y-2">
          {blocks.map((block, idx) => {
            if (block.type === 'text') {
              return (
                <div key={idx} className="whitespace-pre-wrap break-words">
                  {renderFormattedText(block.content)}
                </div>
              )
            } else {
              return (
                <CollapsibleSection
                  key={idx}
                  summary={block.summary || 'Details'}
                  content={block.content}
                  isOpenDefault={block.isOpenDefault || false}
                />
              )
            }
          })}
          {!isUser && message.streaming && (
            <span className="streaming-cursor" aria-hidden="true" />
          )}
        </div>

        {suggestions.length > 0 && onSuggestionClick && (
          <div className="mt-3 pt-2.5 border-t border-slate-200 flex flex-col gap-1.5">
            <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-0.5">
              Suggested follow-ups
            </div>
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((suggestion, idx) => (
                <button
                  key={idx}
                  onClick={() => onSuggestionClick(suggestion)}
                  className="brass-tag px-2.5 py-1 text-xs text-slate-700 hover:text-blue-700 text-left transition-all"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        </div>

        {/* Actions (Outside Bubble) */}
        <div className={`flex items-center px-1 w-full ${isUser ? 'justify-end' : 'justify-between'}`}>
          {!isUser && typeof message.tokensUsed === 'number' && message.tokensUsed > 0 && (
            <div className="flex items-center gap-1 text-[10px] text-slate-400 font-medium">
              <svg
                className="w-3 h-3 text-blue-500"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <circle cx="12" cy="12" r="10" />
                <path d="M12 6v6l4 2" />
              </svg>
              {message.tokensUsed} tokens
            </div>
          )}

          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              handleCopy();
            }}
            className="flex items-center gap-1.5 text-[10px] font-medium transition-colors cursor-pointer text-slate-400 hover:text-blue-600 relative z-10"
            title="Copy message"
          >
            {isCopied ? (
              <><i className="fa-solid fa-check text-emerald-500"></i> Copied</>
            ) : (
              <><i className="fa-regular fa-copy"></i> Copy</>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
