/** Chip compacto de estado de sourcing para listas de campaña. */

const STATE_SHORT = {
  complete: { label: 'Cupo OK', className: 'bg-emerald-100 text-emerald-800' },
  searching: { label: 'Buscando', className: 'bg-sky-100 text-sky-900' },
  paused_provider: { label: 'Pausado créditos', className: 'bg-amber-100 text-amber-950' },
  paused_stall: { label: 'Pausado ICP', className: 'bg-amber-100 text-amber-950' },
}

export function SourcingStatusChip({ status }) {
  if (!status || typeof status !== 'object') {
    return null
  }
  const cfg = STATE_SHORT[status.state]
  if (!cfg) {
    return null
  }
  const imported = Number(status.imported) || 0
  const target = Number(status.target) || 0
  return (
    <span
      className={[
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold',
        cfg.className,
      ].join(' ')}
      title={status.message || cfg.label}
    >
      {cfg.label}
      {target > 0 ? (
        <span className="tabular-nums opacity-80">
          {imported}/{target}
        </span>
      ) : null}
    </span>
  )
}
