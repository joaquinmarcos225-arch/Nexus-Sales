/**
 * @param {{ label: string, value: string, hint?: string, accent?: boolean }} props
 */
export function CreditsStatCard({ label, value, hint, accent = false }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-nx-ink">{label}</p>
      <p className={`mt-1 text-2xl font-bold tabular-nums ${accent ? 'text-nx-brand' : 'text-nx-ink'}`}>
        {value}
      </p>
      {hint ? <p className="mt-1 text-[11px] text-nx-ink">{hint}</p> : null}
    </div>
  )
}
