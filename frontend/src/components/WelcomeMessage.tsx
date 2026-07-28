import React from 'react'
import { useChat } from '../context/ChatContext'
import botLogoUrl from '../assets/icons8-chatbot-96.apng.png'

const QUICK_ACTIONS = [
  { label: 'Are there any offline branches in the network?', question: 'Are there any inactive branches?' },
  { label: 'Compare the status of SALTLAKE and MALDATOWN side-by-side', question: 'Compare BOI-SALTLAKE and BOI-MALDATOWN' },
  { label: 'Show the top 5 branches by alarm count', question: 'Top 5 branches by alarm count' },
  { label: 'Get a category-wise health breakdown of the IoT fleet', question: 'Show category-wise health' }
]

const TAGS = [
  { label: 'Compare Branches', question: 'Compare BOI-SALTLAKE and BOI-MALDATOWN' },
  { label: 'Top Alarms', question: 'Which branch has the most alarms?' },
  { label: 'CCTV Health', question: 'Show category-wise health of CCTV' },
  { label: 'Offline Branches', question: 'Are there any inactive branches?' },
  { label: 'Fleet Health Breakdown', question: 'Show category-wise health' }
]

export const WelcomeMessage: React.FC = () => {
  const { sendMessage } = useChat()

  return (
    <div className="flex-1 flex flex-col w-full py-4 sm:py-8 px-2 sm:px-4">
      <div className="m-auto flex flex-col items-center w-full">
        {/* Bot Icon Badge */}
        <div className="w-11 h-11 sm:w-14 sm:h-14 rounded-2xl flex items-center justify-center mb-3 sm:mb-4 bg-gradient-to-br from-blue-600 to-blue-700 shadow-md border border-blue-400/30">
          <img src={botLogoUrl} alt="Bot Logo" className="w-7 h-7 sm:w-9 sm:h-9 object-contain" />
        </div>

        {/* Headers */}
        <h2 className="text-[#0F172A] text-lg sm:text-2xl font-bold text-center tracking-tight mb-1">
          SAI Assistant
        </h2>
        <p className="text-[#64748B] text-[11px] sm:text-xs text-center max-w-xs sm:max-w-xl px-2 mb-5 sm:mb-7 leading-relaxed font-medium">
          I am SAI, your smart assistant
        </p>

        {/* Quick Actions */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 sm:gap-3 w-full max-w-xl mb-5 sm:mb-7">
          {QUICK_ACTIONS.map((action) => (
            <button
              key={action.question}
              onClick={() => sendMessage(action.question)}
              className="paper-card text-left p-3 sm:p-4 flex items-start gap-2.5 sm:gap-3 cursor-pointer group"
            >
              <span className="text-[#2563EB] font-bold text-xs sm:text-sm leading-none mt-0.5 group-hover:translate-x-0.5 transition-transform">
                ➔
              </span>
              <span className="text-xs text-[#1E293B] font-semibold leading-snug">{action.label}</span>
            </button>
          ))}
        </div>

        {/* Suggestion Tags */}
        <div className="flex flex-wrap gap-1.5 sm:gap-2 justify-center max-w-xl">
          {TAGS.map((tag) => (
            <button
              key={tag.label}
              onClick={() => sendMessage(tag.question)}
              className="brass-tag px-3 py-1.5 sm:px-3.5 sm:py-2 text-[11px] sm:text-xs text-[#334155]"
            >
              {tag.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
