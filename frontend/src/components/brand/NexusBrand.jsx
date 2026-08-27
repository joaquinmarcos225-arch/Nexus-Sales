import { useId, useMemo } from 'react'
import { APP_NAME, pickAppSlogan } from '../../utils/constants.js'

/**
 * Isotipo abstracto — núcleo + anillos + planetitas en órbita.
 * @param {{ size?: number, className?: string, title?: string }} props
 */
export function NexusLogoMark({ size = 36, className = '', title = APP_NAME }) {
  const uid = useId().replace(/:/g, '')
  const beamId = `nx-logo-beam-${uid}`
  const coreId = `nx-logo-core-${uid}`
  const glowId = `nx-logo-glow-${uid}`

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={['nx-logo-mark', className].filter(Boolean).join(' ')}
      role="img"
      aria-label={title}
    >
      <title>{title}</title>
      <defs>
        <linearGradient id={beamId} x1="8" y1="12" x2="56" y2="52" gradientUnits="userSpaceOnUse">
          <stop stopColor="#ffe4e6" />
          <stop offset="0.35" stopColor="#f87171" />
          <stop offset="1" stopColor="#b91c1c" />
        </linearGradient>
        <radialGradient id={coreId} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="35%" stopColor="#fecaca" />
          <stop offset="70%" stopColor="#f87171" />
          <stop offset="100%" stopColor="#dc2626" />
        </radialGradient>
        <filter id={glowId} x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="2.6" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <circle cx="32" cy="32" r="27" stroke="rgba(248,113,113,0.35)" strokeWidth="1" strokeDasharray="3 5" />
      <g className="nx-logo-ring nx-logo-ring--a">
        <ellipse
          cx="32"
          cy="32"
          rx="23"
          ry="9.5"
          stroke="rgba(254,202,202,0.75)"
          strokeWidth="1.6"
          transform="rotate(-18 32 32)"
          fill="none"
        />
      </g>
      <g className="nx-logo-ring nx-logo-ring--b">
        <ellipse
          cx="32"
          cy="32"
          rx="23"
          ry="9.5"
          stroke="rgba(248,113,113,0.55)"
          strokeWidth="1.3"
          transform="rotate(48 32 32)"
          fill="none"
        />
      </g>
      <g className="nx-logo-ring nx-logo-ring--c">
        <ellipse
          cx="32"
          cy="32"
          rx="17"
          ry="7"
          stroke="rgba(255,241,242,0.5)"
          strokeWidth="1.1"
          transform="rotate(108 32 32)"
          fill="none"
        />
      </g>
      <path
        d="M12 44 C18 28, 28 18, 40 20 C50 22, 54 30, 48 40 C42 50, 28 52, 20 46"
        stroke={`url(#${beamId})`}
        strokeWidth="3"
        strokeLinecap="round"
        fill="none"
        filter={`url(#${glowId})`}
      />
      <path
        d="M50 18 C42 26, 36 38, 28 44 C22 48, 16 46, 18 38"
        stroke="rgba(254,202,202,0.75)"
        strokeWidth="1.8"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="32" cy="32" r="6" fill={`url(#${coreId})`} filter={`url(#${glowId})`} />
      <circle cx="32" cy="32" r="2.5" fill="#fff" opacity="0.95" />

      {/* Planetas — radios distintos para órbitas visibles */}
      <g className="nx-logo-orbit nx-logo-orbit--1">
        <circle cx="14" cy="22" r="2.1" fill="#fecaca" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--2">
        <circle cx="50" cy="20" r="2.3" fill="#f87171" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--3">
        <circle cx="46" cy="44" r="1.9" fill="#fff1f2" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--4">
        <circle cx="18" cy="46" r="1.7" fill="#ef4444" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--5">
        <circle cx="32" cy="8" r="1.8" fill="#fca5a5" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--6">
        <circle cx="56" cy="32" r="2" fill="#fb7185" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--7">
        <circle cx="32" cy="55" r="1.5" fill="#fecdd3" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--8">
        <circle cx="9" cy="34" r="1.6" fill="#f87171" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--9">
        <circle cx="22" cy="12" r="1.3" fill="#fda4af" />
      </g>
      <g className="nx-logo-orbit nx-logo-orbit--10">
        <circle cx="48" cy="52" r="1.4" fill="#e11d48" />
      </g>
    </svg>
  )
}

/** S tipográfica (wordmark). La S grande de login está en TransitionNBurst. */
export function NexusSMark({ className = '' }) {
  return (
    <span className={['nx-brand-letter font-display font-extrabold text-nx-brand', className].filter(Boolean).join(' ')}>
      S
    </span>
  )
}

export function NexusWordmark({
  compact = false,
  showSlogan = false,
  sloganText = null,
  light = true,
  large = false,
  className = '',
}) {
  const slogan = useMemo(() => {
    if (!showSlogan) return ''
    if (sloganText) return sloganText
    return pickAppSlogan()
  }, [showSlogan, sloganText])
  const ink = light ? 'text-zinc-100' : 'text-nx-ink'
  const soft = light ? 'text-zinc-400' : 'text-nx-muted'
  const titleSize = compact
    ? 'text-[12px]'
    : large
      ? 'text-[1.05rem] tracking-[0.06em] sm:text-[1.15rem] sm:tracking-[0.08em]'
      : 'text-[15px] sm:text-base'

  return (
    <div className={['min-w-0 leading-none', className].filter(Boolean).join(' ')}>
      <p className={['truncate font-sans', titleSize, ink].join(' ')}>
        <span className="nx-brand-letter font-display font-extrabold text-nx-brand">N</span>
        <span className={['font-semibold', soft].join(' ')}>exus</span>
        {' '}
        <span className="nx-brand-letter font-display font-extrabold text-nx-brand">S</span>
        <span className={['font-semibold', soft].join(' ')}>ales</span>
      </p>
      {showSlogan && slogan ? (
        <p className="nx-brand-slogan mt-2 max-w-[18rem] text-center text-[11px] font-light leading-snug tracking-wide text-zinc-300/90 normal-case sm:text-[12px] sm:leading-relaxed">
          {slogan}
        </p>
      ) : null}
    </div>
  )
}

export function NexusBrandHero({ showSlogan = false, size = 'md' }) {
  const markSize = size === 'lg' ? 96 : size === 'sm' ? 52 : 80
  const sloganMax = size === 'sm' ? 'max-w-[18rem]' : 'max-w-[24rem]'
  const largeWordmark = size !== 'sm'
  return (
    <div className="flex flex-col items-center text-center">
      <NexusLogoMark
        size={markSize}
        className="drop-shadow-[0_0_28px_rgba(248,113,113,0.7)]"
      />
      <div className={size === 'sm' ? 'mt-2.5' : 'mt-4'}>
        <NexusWordmark light large={largeWordmark} showSlogan={showSlogan} className={sloganMax} />
      </div>
    </div>
  )
}
