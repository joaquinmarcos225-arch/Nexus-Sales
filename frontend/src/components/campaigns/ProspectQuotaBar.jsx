/**
 * Progreso de importación vs meta ICP (prospect_count).
 */
export function ProspectQuotaBar({
  current,
  target,
  className = '',
  compact = false,
  hint,
  'data-tour': dataTour,
}) {
  const cur = Math.max(0, Number(current) || 0)
  const tgt = Math.max(0, Number(target) || 0)
  if (tgt <= 0) {
    return null
  }
  const pct = Math.min(100, Math.round((cur / tgt) * 100))
  const met = cur >= tgt
  const remaining = Math.max(0, tgt - cur)

  return (
    <div className={className} data-tour={dataTour}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-medium text-nx-ink">
        <span>Meta ICP (búsqueda)</span>
        <span className="tabular-nums text-nx-ink">
          {cur} / {tgt}
          <span className="ml-1 text-nx-ink/70">({pct}%)</span>
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-nx-border/60">
        <div
          className={[
            'h-full rounded-full transition-all duration-500',
            met ? 'bg-gradient-to-r from-red-600 to-red-500' : 'bg-gradient-to-r from-nx-brand to-red-500',
          ].join(' ')}
          style={{ width: `${pct}%` }}
        />
      </div>
      {!compact ? (
        <p className="mt-1.5 text-[10px] leading-relaxed text-nx-ink">
          {hint ||
            (met
              ? 'Cupo completo. Nexus puede seguir con outreach sobre los importados.'
              : `Faltan ${remaining} para la meta ICP. Al activar la campaña, Nexus importa y rellena en background.`)}
        </p>
      ) : null}
    </div>
  )
}
