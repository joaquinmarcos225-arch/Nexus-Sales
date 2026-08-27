import { useEffect, useMemo, useState } from 'react'

const DISPLAY_SECONDS = 120

function formatClock(totalSec) {
  const sec = Math.max(0, Math.floor(Number(totalSec) || 0))
  const mm = String(Math.floor(sec / 60)).padStart(2, '0')
  const ss = String(sec % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/**
 * Aviso ligero: buscando canales faltantes + countdown 120s hacia atrás (mm:ss).
 */
export function ChannelEnrichCountdown({
  active = false,
  label = 'Buscando información de canales…',
  detail = null,
  deadlineAt = null,
  maxSeconds = DISPLAY_SECONDS,
  done = false,
  resultText = null,
}) {
  const [now, setNow] = useState(() => Date.now())
  const [startedAt, setStartedAt] = useState(null)

  // Cuenta visual fija: 120s desde que empieza a mostrarse (no el deadline largo del backend).
  const displayTotal = Math.min(
    DISPLAY_SECONDS,
    Math.max(1, Math.floor(Number(maxSeconds) || DISPLAY_SECONDS)),
  )

  useEffect(() => {
    if (!active || done) {
      setStartedAt(null)
      return undefined
    }
    setStartedAt(Date.now())
    setNow(Date.now())
    const id = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(id)
  }, [active, done])

  // Si hay deadline del server más corto que 120s, respetarlo.
  const serverRemainingSec = useMemo(() => {
    if (!deadlineAt) return null
    const t = new Date(deadlineAt).getTime()
    if (!Number.isFinite(t)) return null
    return Math.max(0, Math.ceil((t - now) / 1000))
  }, [deadlineAt, now])

  if (!active && !done) return null

  const elapsedSec = startedAt ? Math.max(0, (now - startedAt) / 1000) : 0
  let remainingSec = Math.max(0, Math.ceil(displayTotal - elapsedSec))
  if (serverRemainingSec != null) {
    remainingSec = Math.min(remainingSec, serverRemainingSec)
  }
  remainingSec = Math.min(displayTotal, Math.max(0, remainingSec))
  const clock = formatClock(remainingSec)
  const elapsedFrac = Math.min(1, Math.max(0, 1 - remainingSec / displayTotal))
  const cleanLabel = String(label || 'Buscando información de canales…')
    .replace(/\s*…\s*$/, '')
    .replace(/\s*\.\.\.\s*$/, '')

  if (done) {
    return (
      <div
        className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2.5 text-xs text-zinc-700"
        role="status"
      >
        <p className="font-medium text-nx-ink">{resultText || 'Listo. Seguimos con los datos disponibles.'}</p>
      </div>
    )
  }

  return (
    <div
      className="rounded-lg border border-zinc-200 bg-zinc-50/90 px-3 py-2.5 text-xs text-zinc-700 shadow-sm"
      role="status"
      aria-live="polite"
    >
      <p className="font-medium text-nx-ink">
        {cleanLabel}…{' '}
        <span className="tabular-nums font-semibold text-zinc-900">{clock}</span>
      </p>
      {detail ? <p className="mt-1 text-[11px] text-zinc-600">{detail}</p> : null}
      <div className="mt-2 h-1 overflow-hidden rounded-full bg-zinc-200">
        <div
          className="h-full rounded-full bg-zinc-500/70 transition-[width] duration-300 ease-linear"
          style={{ width: `${Math.round(elapsedFrac * 100)}%` }}
        />
      </div>
      <p className="mt-1.5 text-[10px] text-zinc-500">
        Cuenta regresiva de 2 minutos. Si no aparecen a tiempo, la secuencia sigue con lo que haya.
      </p>
    </div>
  )
}
