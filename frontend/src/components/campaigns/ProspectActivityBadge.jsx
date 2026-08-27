import { useEffect, useMemo, useState } from 'react'
import { resolveProspectActivity } from '../../utils/prospectActivity.js'

function useCountdown(deadlineAt, active) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active || !deadlineAt) return undefined
    const id = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(id)
  }, [active, deadlineAt])

  return useMemo(() => {
    if (!deadlineAt) return null
    const t = new Date(deadlineAt).getTime()
    if (!Number.isFinite(t)) return null
    // Mostrar como mm:ss y topear en 120s (misma UX que el banner de búsqueda).
    const sec = Math.min(120, Math.max(0, Math.ceil((t - now) / 1000)))
    const mm = String(Math.floor(sec / 60)).padStart(2, '0')
    const ss = String(sec % 60).padStart(2, '0')
    return `${mm}:${ss}`
  }, [deadlineAt, now])
}

const TONE_CLASS = {
  search: 'text-amber-800',
  wait: 'text-sky-800',
  active: 'text-nx-ink',
  ok: 'text-emerald-800',
  muted: 'text-nx-muted',
}

/**
 * Badge al costado del prospecto: actividad + Gmail / LinkedIn / WhatsApp.
 */
export function ProspectActivityBadge({ prospect, className = '' }) {
  const act = resolveProspectActivity(prospect)
  const countdown = useCountdown(act?.deadlineAt, Boolean(act?.showCountdown))
  const toneClass = TONE_CLASS[act?.tone] || TONE_CLASS.muted
  const findSummary = String(prospect?.channel_find_summary || prospect?.channel_enrich_message || '').trim()
  const showFind =
    findSummary &&
    (/gmail|whatsapp|linkedin|buscando|falta/i.test(findSummary) ||
      /encontrado/i.test(findSummary))
  const showLabel = Boolean(act?.label)

  if (!showLabel && !showFind) return null

  return (
    <div className={className}>
      {showLabel ? (
      <p className={`text-[11px] font-medium ${toneClass}`.trim()} title={act.label}>
        <span>{act.label.replace(/\s*…\s*$/, '').replace(/\s*\.\.\.\s*$/, '')}</span>
        {act.showCountdown && countdown ? (
          <span className="ml-1 tabular-nums font-semibold text-zinc-900">· {countdown}</span>
        ) : act.showCountdown ? (
          <span className="ml-1 text-zinc-500">…</span>
        ) : null}
      </p>
      ) : null}
      {showFind ? (
        <p
          className={`mt-0.5 text-[11px] ${
            /no encontrado|falta/i.test(findSummary) && !/buscando/i.test(findSummary)
              ? 'font-medium text-amber-900'
              : /buscando/i.test(findSummary)
                ? 'font-medium text-amber-800'
                : 'font-medium text-emerald-800'
          }`}
          title={findSummary}
        >
          {findSummary}
          {act?.showCountdown && countdown && !findSummary.includes(countdown) ? (
            <span className="ml-1 tabular-nums font-semibold text-zinc-900">· {countdown}</span>
          ) : null}
        </p>
      ) : null}
    </div>
  )
}
