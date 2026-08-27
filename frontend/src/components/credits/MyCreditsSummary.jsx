import { formatContactCredits } from '../../utils/format.js'
import { CreditsUsageBar } from './CreditsUsageBar.jsx'

/**
 * @param {{
 *   available: number,
 *   allocated?: number,
 *   used?: number,
 *   roleScope?: 'director_pool' | 'personal',
 *   compact?: boolean,
 * }} props
 */
export function MyCreditsSummary({
  available,
  allocated = 0,
  used = 0,
  roleScope = 'personal',
  compact = false,
}) {
  const isDirector = roleScope === 'director_pool'

  if (compact) {
    return (
      <div className="text-right">
        <p className="text-2xl font-semibold text-nx-ink tabular-nums">
          {(Number(available) || 0).toLocaleString('es-AR')}
        </p>
        <p className="text-[11px] text-nx-ink">créditos disponibles</p>
      </div>
    )
  }

  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-nx-ink">Mis créditos</p>
      <p className="mt-1 text-2xl font-bold tabular-nums text-nx-brand">
        {formatContactCredits(available)}
      </p>
      <p className="mt-1 text-sm text-nx-ink">
        {isDirector
          ? 'Disponibles para asignar al equipo (SDR o Manager).'
          : 'Disponibles para crear campañas y prospectar.'}
      </p>
      {!isDirector ? (
        <>
          <p className="mt-2 text-sm text-nx-ink">
            Asignados: {formatContactCredits(allocated)} · Usados: {formatContactCredits(used)}
          </p>
          {Number(allocated) > 0 ? (
            <div className="mt-3 max-w-md">
              <CreditsUsageBar used={used} total={allocated} label="Uso de tus créditos" />
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
