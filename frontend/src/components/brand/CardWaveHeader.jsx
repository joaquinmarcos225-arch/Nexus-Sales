import { useId } from 'react'

/**
 * Ondas rojas (misma familia visual que sidebar/topbar).
 * @param {{
 *   variant?: 'dark' | 'light',
 *   className?: string,
 *   height?: 'sm' | 'md',
 * }} props
 */
export function CardWaveHeader({ variant = 'dark', className = '', height = 'md' }) {
  const uid = useId().replace(/:/g, '')
  const gradId = `nx-card-wave-grad-${uid}`
  const hClass = height === 'sm' ? 'h-14' : 'h-[4.5rem]'
  const isDark = variant === 'dark'
  const red = '185,28,28'

  return (
    <div
      className={[
        'nx-card-wave-header relative overflow-hidden',
        hClass,
        isDark ? 'bg-[#050505]' : 'bg-gradient-to-br from-red-50 to-white',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      aria-hidden
    >
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="0 0 400 72"
        preserveAspectRatio="none"
        fill="none"
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={`rgba(${red},0.55)`} />
            <stop offset="55%" stopColor={`rgba(${red},0.22)`} />
            <stop offset="100%" stopColor={`rgba(${red},0)`} />
          </linearGradient>
        </defs>
        <g className="nx-wave-drift nx-wave-drift--a">
          <path
            d="M0 72 C 80 20, 160 58, 240 28 C 320 0, 360 48, 400 24 L400 72 Z"
            fill={`rgba(${red},0.12)`}
          />
          <path
            d="M0 44 C 100 18, 200 52, 300 22 C 350 8, 380 38, 400 30"
            stroke={`url(#${gradId})`}
            strokeWidth="1.6"
          />
          <path
            d="M0 52 C 120 30, 220 60, 340 36 C 370 28, 390 46, 400 40"
            stroke={`rgba(${red},0.28)`}
            strokeWidth="1"
          />
        </g>
        <g className="nx-wave-drift nx-wave-drift--b">
          <circle cx="320" cy="20" r="2" fill={`rgba(${red},0.45)`} className="nx-wave-node" />
          <circle cx="280" cy="48" r="1.5" fill={`rgba(${red},0.4)`} className="nx-wave-node" />
          <circle cx="360" cy="42" r="1.2" fill={`rgba(${red},0.35)`} className="nx-wave-node" />
        </g>
      </svg>
    </div>
  )
}
