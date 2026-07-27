import React from 'react'

export const BotLogoSvg: React.FC<{ className?: string }> = ({ className = 'w-full h-full' }) => {
  return (
    <svg
      viewBox="0 0 100 100"
      className={className}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        {/* Orange Gradient matching ThingsBoard #EE6E20 theme */}
        <linearGradient id="tb-orange-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#F97316" />
          <stop offset="50%" stopColor="#EE6E20" />
          <stop offset="100%" stopColor="#C2410C" />
        </linearGradient>
        
        {/* Soft Glow filter for the central node */}
        <filter id="tb-node-glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      {/* Inner Speech Bubble (Deep Slate) */}
      <path
        d="M50 12C28.4 12 11 27.9 11 47.5c0 7.8 2.8 15 7.5 20.8L14 85l13.5-4.1c6.3 3.5 13.6 5.6 22.5 5.6 21.6 0 39-15.9 39-35.5S71.6 12 50 12z"
        fill="#0F172A"
      />

      {/* Orbital Connection Ring */}
      <circle
        cx="50"
        cy="47.5"
        r="22"
        stroke="url(#tb-orange-grad)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray="65 25"
      />

      {/* Inner Curved Network Lines */}
      <path
        d="M36 47.5c0-7.7 6.3-14 14-14s14 6.3 14 14c0 3.8-1.5 7.3-4 9.9L50 67.5"
        stroke="url(#tb-orange-grad)"
        strokeWidth="2.5"
        strokeLinecap="round"
      />

      {/* Glow nodes (Dots) */}
      {/* Center Main Node */}
      <circle
        cx="50"
        cy="47.5"
        r="5.5"
        fill="url(#tb-orange-grad)"
        filter="url(#tb-node-glow)"
      />
      {/* Top Right Node */}
      <circle cx="64" cy="33.5" r="4" fill="url(#tb-orange-grad)" />
      {/* Bottom Connection Node */}
      <circle cx="50" cy="67.5" r="4.5" fill="url(#tb-orange-grad)" />
      {/* Left Network Node */}
      <circle cx="28" cy="47.5" r="3.5" fill="url(#tb-orange-grad)" />
      {/* Right Network Node */}
      <circle cx="72" cy="47.5" r="3.5" fill="url(#tb-orange-grad)" />

      {/* Connector Lines */}
      <line x1="50" y1="47.5" x2="64" y2="33.5" stroke="url(#tb-orange-grad)" strokeWidth="2" />
      <line x1="50" y1="47.5" x2="50" y2="67.5" stroke="url(#tb-orange-grad)" strokeWidth="2" />
      <line x1="28" y1="47.5" x2="36" y2="47.5" stroke="url(#tb-orange-grad)" strokeWidth="1.5" strokeDasharray="2 2" />
      <line x1="64" y1="47.5" x2="72" y2="47.5" stroke="url(#tb-orange-grad)" strokeWidth="1.5" strokeDasharray="2 2" />
    </svg>
  )
}
