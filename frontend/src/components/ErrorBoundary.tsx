import React from 'react'

interface State {
  hasError: boolean
}

/**
 * Catches runtime errors in the chat UI so a render/streaming bug can't crash the entire embedded
 * widget inside ThingsBoard (audit #22). Shows a friendly retry fallback instead of a blank frame.
 */
export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: unknown) {
    console.error('Chat widget crashed:', error)
  }

  private handleReset = () => {
    this.setState({ hasError: false })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="w-full h-full flex flex-col items-center justify-center gap-3 p-6 text-center bg-[#faf8f5]">
          <p className="text-sm text-[#1C1917]">Something went wrong in the chat widget.</p>
          <button
            onClick={this.handleReset}
            className="brass-button px-4 py-2 text-sm"
          >
            Reload chat
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
