import { formatLocalDateTime } from '../../utils/instantFormat.js'

const STATE_STYLE = {
  complete: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  searching: 'border-sky-200 bg-sky-50 text-sky-900',
  paused_provider: 'border-amber-200 bg-amber-50 text-amber-950',
  paused_stall: 'border-amber-200 bg-amber-50 text-amber-950',
  no_quota: 'border-nx-border bg-nx-card-muted text-nx-muted',
}

const STATE_LABEL = {
  complete: 'Cupo completo',
  searching: 'Buscando prospectos',
  paused_provider: 'Pausado — créditos',
  paused_stall: 'Pausado — sin matches',
  no_quota: 'Sin meta ICP',
}

/**
 * Banner de estado de sourcing (cupo, pausa, créditos).
 * @param {{ status?: object | null, compact?: boolean }} props
 */
export function CampaignSourcingStatus({ status, compact = false }) {
  if (!status || typeof status !== 'object') {
    return null
  }

  const state = status.state || 'searching'
  const style = STATE_STYLE[state] || STATE_STYLE.searching
  const label = STATE_LABEL[state] || 'Sourcing'
  const retryAfter = status.retry_after
    ? formatLocalDateTime(status.retry_after, 'es-AR')
    : null

  return (
    <div className={['rounded-xl border px-4 py-3', style, compact ? 'text-[11px]' : 'text-xs'].join(' ')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold">{label}</p>
        {Number(status.target) > 0 ? (
          <span className="tabular-nums font-medium">
            {Number(status.imported) || 0} / {Number(status.target)}
          </span>
        ) : null}
      </div>
      {status.message ? (
        <p className={`mt-1 leading-relaxed ${compact ? '' : 'text-[11px]'} opacity-90`}>{status.message}</p>
      ) : null}
      {retryAfter && (state === 'paused_stall' || state === 'paused_provider') ? (
        <p className="mt-1 text-[10px] opacity-75">Reintento automático estimado: {retryAfter}</p>
      ) : null}
      {Number(status.zero_import_ticks) > 0 && state === 'searching' ? (
        <p className="mt-1 text-[10px] opacity-75">
          Rondas recientes sin importar: {status.zero_import_ticks}
        </p>
      ) : null}
    </div>
  )
}
