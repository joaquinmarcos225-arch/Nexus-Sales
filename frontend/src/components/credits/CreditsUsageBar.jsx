/**
 * Barra de uso de créditos estilo "usage meter": muestra el porcentaje
 * consumido de 0 a 100% en rojo.
 *
 * @param {{
 *   used: number,
 *   total: number,
 *   label?: string,
 *   unit?: string,
 * }} props
 */
export function CreditsUsageBar({ used, total, label = 'Uso de créditos', unit = 'créditos' }) {
  const totalNum = Math.max(0, Number(total) || 0)
  const usedNum = Math.max(0, Number(used) || 0)
  const pct = totalNum > 0 ? Math.min(100, Math.round((usedNum / totalNum) * 100)) : 0

  return (
    <div>
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-nx-ink">{label}</span>
        <span className="font-semibold tabular-nums text-nx-brand">{pct}%</span>
      </div>
      <div
        className="mt-1.5 h-2.5 w-full overflow-hidden rounded-full bg-nx-card-muted"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className="h-full rounded-full bg-nx-brand transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1 text-[11px] tabular-nums text-nx-ink">
        {usedNum.toLocaleString('es-AR')} / {totalNum.toLocaleString('es-AR')} {unit} usados
      </p>
    </div>
  )
}
