/**
 * Overlay del chrome: degradé rojo/negro estático (sin líneas ni nodos animados).
 *
 * @param {{ variant: 'sidebar' | 'topbar' }} props
 */
export function ChromeWaveOverlay({ variant }) {
  if (variant === 'topbar') {
    return (
      <div className="nx-chrome-wave-overlay pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
        <div
          className="h-full w-full"
          style={{
            background:
              'linear-gradient(90deg, rgba(220,38,38,0.28) 0%, rgba(127,29,29,0.12) 40%, rgba(12,6,6,0) 100%)',
          }}
        />
      </div>
    )
  }

  return (
    <div className="nx-chrome-wave-overlay pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      <div
        className="h-full w-full"
        style={{
          background:
            'linear-gradient(180deg, rgba(220,38,38,0.26) 0%, rgba(127,29,29,0.1) 35%, rgba(12,6,6,0.02) 70%, transparent 100%)',
        }}
      />
    </div>
  )
}
