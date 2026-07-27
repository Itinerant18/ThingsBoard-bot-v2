import React from 'react'

export const TypingIndicator: React.FC = () => {
  return (
    <div className="flex justify-start message-enter">
      <div className="bg-white border border-slate-200 shadow-sm rounded-2xl rounded-bl-none px-4 py-3 flex gap-1.5 items-center">
        <span className="w-2 h-2 rounded-full bg-blue-600 typing-dot" />
        <span className="w-2 h-2 rounded-full bg-blue-600 typing-dot" />
        <span className="w-2 h-2 rounded-full bg-blue-600 typing-dot" />
      </div>
    </div>
  )
}
