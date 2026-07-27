import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        stone: {
          50: '#faf7f2',
          100: '#f0ebe3',
          950: '#1c1917',
          900: '#44403c',
          800: '#78716c',
          700: '#a8a29e',
          600: '#57534e',
          400: '#d6cfc4'
        },
        accent: {
          gold: '#ca8a04',
          teal: '#0d9488'
        }
      },
      fontFamily: {
        sans: ['"Trebuchet MS"', 'sans-serif'],
        display: ['"DM Sans"', 'sans-serif']
      },
      spacing: {
        unit: '8px'
      }
    }
  },
  plugins: []
} satisfies Config
