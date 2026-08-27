/**
 * Isotipo Nexus — marca roja, N blanca (legible en sidebar y header).
 * @param {{ size?: number, className?: string, title?: string }} props
 */
export function NexusMark({ size = 36, className = '', title = 'Nexus Sales' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label={title}
    >
      <title>{title}</title>
      <rect x="2" y="2" width="36" height="36" rx="9" fill="#dc2626" />
      <path
        d="M12.5 11.5h3.5v9.8L25.5 11.5H29v17h-3.5v-9.8L16.5 28.5h-3.5v-17z"
        fill="#ffffff"
      />
    </svg>
  )
}
