/**
 * @param {{
 *   children: import('react').ReactNode
 *   className?: string
 *   elevated?: boolean
 *   muted?: boolean
 *   padding?: 'none' | 'sm' | 'md' | 'lg'
 * }} props
 */
export function Card({
  children,
  className = '',
  elevated = false,
  muted = false,
  padding = 'md',
}) {
  const pad =
    padding === 'none'
      ? ''
      : padding === 'sm'
        ? 'p-3'
        : padding === 'lg'
          ? 'p-6'
          : 'p-4'
  const base = muted ? 'nx-card-muted' : elevated ? 'nx-card-elevated' : 'nx-card'
  return <div className={[base, pad, className].filter(Boolean).join(' ')}>{children}</div>
}

/**
 * @param {{ label: string; value: string | number; hint?: string; className?: string }} props
 */
export function StatCard({ label, value, hint, className = '' }) {
  return (
    <Card className={className} padding="md">
      <p className="nx-kicker text-nx-muted">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-nx-ink tabular-nums">{value}</p>
      {hint ? <p className="mt-1.5 text-xs leading-relaxed text-nx-muted">{hint}</p> : null}
    </Card>
  )
}

/**
 * @param {{ title: string; children: import('react').ReactNode; className?: string; action?: import('react').ReactNode }} props
 */
export function Panel({ title, children, className = '', action = null }) {
  return (
    <Card className={className} padding="md">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="nx-kicker text-nx-muted">{title}</p>
        {action}
      </div>
      <div className="mt-3">{children}</div>
    </Card>
  )
}
