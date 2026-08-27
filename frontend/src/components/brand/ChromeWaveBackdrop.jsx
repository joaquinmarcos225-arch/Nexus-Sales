/**
 * Fondo futurista negro con ondas rojas sutiles — compartido sidebar + topbar.
 * Las ondas se alinean en la esquina superior del área de contenido.
 * @param {{ variant: 'sidebar' | 'topbar' }} props
 */
export function ChromeWaveBackdrop({ variant }) {
  const isSidebar = variant === 'sidebar'

  return (
    <div className="nx-chrome-wave-layer pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {/* Brillo de unión esquina sidebar ↔ topbar */}
      <div
        className={[
          'absolute bg-[radial-gradient(circle_at_center,rgba(220,38,38,0.22),transparent_68%)]',
          isSidebar ? '-right-8 top-0 h-28 w-28' : '-left-6 top-0 h-24 w-32',
        ].join(' ')}
      />

      <svg
        className="absolute inset-0 h-full w-full"
        preserveAspectRatio="none"
        viewBox={isSidebar ? '0 0 208 720' : '0 0 1200 56'}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <linearGradient id={`nx-wave-stroke-${variant}`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="rgba(220,38,38,0)" />
            <stop offset="45%" stopColor="rgba(220,38,38,0.35)" />
            <stop offset="100%" stopColor="rgba(248,113,113,0.12)" />
          </linearGradient>
          <linearGradient id={`nx-wave-fill-${variant}`} x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="rgba(220,38,38,0.07)" />
            <stop offset="100%" stopColor="rgba(220,38,38,0)" />
          </linearGradient>
        </defs>

        {isSidebar ? (
          <>
            <path
              d="M208 0 C 140 90, 168 210, 208 320 S 120 520, 208 720"
              stroke={`url(#nx-wave-stroke-${variant})`}
              strokeWidth="1.2"
              fill="none"
            />
            <path
              d="M208 40 C 150 130, 175 250, 208 360 S 130 560, 208 680"
              stroke="rgba(220,38,38,0.14)"
              strokeWidth="0.8"
              fill="none"
            />
            <path
              d="M208 0 L208 720 L120 720 C 150 560, 130 360, 160 180 C 175 90, 195 40, 208 0 Z"
              fill={`url(#nx-wave-fill-${variant})`}
            />
            <path
              d="M0 0 C 60 40, 40 100, 90 140 S 30 220, 80 280"
              stroke="rgba(220,38,38,0.1)"
              strokeWidth="0.7"
              fill="none"
            />
          </>
        ) : (
          <>
            <path
              d="M0 56 C 180 28, 320 44, 520 30 S 880 18, 1200 42"
              stroke={`url(#nx-wave-stroke-${variant})`}
              strokeWidth="1.2"
              fill="none"
            />
            <path
              d="M0 48 C 220 22, 400 38, 640 26 S 960 14, 1200 36"
              stroke="rgba(220,38,38,0.16)"
              strokeWidth="0.8"
              fill="none"
            />
            <path
              d="M0 56 L1200 56 L1200 36 C 900 20, 600 32, 300 24 C 140 18, 60 40, 0 56 Z"
              fill={`url(#nx-wave-fill-${variant})`}
            />
            <path
              d="M0 8 C 120 20, 240 4, 400 16 S 720 28, 960 12"
              stroke="rgba(220,38,38,0.08)"
              strokeWidth="0.7"
              fill="none"
            />
          </>
        )}
      </svg>

      <div
        className={
          isSidebar
            ? 'absolute right-0 top-0 h-full w-px bg-gradient-to-b from-red-500/10 via-red-500/35 to-red-500/10'
            : 'absolute bottom-0 left-0 h-px w-full bg-gradient-to-r from-red-500/40 via-red-500/18 to-transparent'
        }
      />
    </div>
  )
}
