import { Component } from 'react'

/**
 * Evita pantalla en blanco si el panel lanza en render.
 */
export class LeadSourcingErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('[Lead Sourcing] render error:', error, info)
  }

  render() {
    const { error } = this.state
    if (error) {
      return (
        <section className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">Error al mostrar Lead Sourcing</p>
          <p className="mt-1 text-xs">{error instanceof Error ? error.message : String(error)}</p>
          <button
            type="button"
            className="mt-3 rounded-md bg-red-100 px-3 py-1.5 text-xs font-semibold text-red-950 hover:bg-red-200"
            onClick={() => this.setState({ error: null })}
          >
            Reintentar render
          </button>
        </section>
      )
    }
    return this.props.children
  }
}
