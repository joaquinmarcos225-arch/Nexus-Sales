import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchAutomationHealth } from '../../utils/api.js'

/**
 * Estado del motor automático del servidor (scheduler).
 */
export function AutomationStatusBanner({ sequenceRunning = false }) {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await fetchAutomationHealth()
      setHealth(data && typeof data === 'object' ? data : null)
      setLoadError(!data)
    } catch {
      setHealth(null)
      setLoadError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => {
      void load()
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [load])

  if (loading) {
    return (
      <p className="text-xs text-nx-muted" role="status">
        Verificando motor automático del servidor…
      </p>
    )
  }

  if (loadError || !health) {
    return (
      <div className="rounded-xl border border-nx-border bg-nx-card-muted px-4 py-3 text-sm text-nx-ink">
        No se pudo comprobar el scheduler. Revisá que el backend esté corriendo en el puerto 8002.
      </div>
    )
  }

  const schedulerOk = health.scheduler_running === true
  const realMode = health.real_mode === true
  const gmailAuto = health.gmail_automation_active === true
  const sequenceTouches = health.sequence_touches_scheduler_enabled === true

  if (schedulerOk && (!realMode || gmailAuto)) {
    return (
      <div
        className="rounded-xl border border-red-200 bg-red-50/90 px-4 py-3 text-sm text-red-950 shadow-sm"
        role="status"
      >
        <p className="font-semibold">Motor automático activo</p>
        <p className="mt-1 text-xs leading-relaxed text-red-900/90">
          {sequenceRunning
            ? 'El scheduler del servidor está corriendo. La secuencia puede enviar emails, toques programados y follow-ups sola.'
            : 'Cuando inicies la secuencia, Nexus avanzará solo sin que refresques la página.'}
          {sequenceTouches ? ' Toques días 4–19 activos.' : ''}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {!schedulerOk ? (
        <div
          className="rounded-xl border border-zinc-300 bg-zinc-50 px-4 py-3 text-sm text-zinc-950 shadow-sm"
          role="alert"
        >
          <p className="font-semibold">
            {sequenceRunning
              ? 'La secuencia está iniciada, pero el motor automático del servidor está apagado'
              : 'Motor automático apagado — la secuencia no avanzará sola'}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-zinc-900/90">
            Activá{' '}
            <code className="rounded bg-zinc-100/80 px-1 py-0.5 text-[11px]">NEXUS_AUTOMATION_SCHEDULER=1</code>{' '}
            en <code className="text-[11px]">backend/.env</code> y reiniciá uvicorn.
          </p>
        </div>
      ) : null}

      {realMode && schedulerOk && !gmailAuto ? (
        <div className="rounded-xl border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-950 shadow-sm">
          <p className="font-semibold">Gmail automático desactivado en el servidor</p>
          <p className="mt-1 text-xs leading-relaxed text-zinc-900/90">
            Modo real sin <code className="rounded bg-zinc-100/80 px-1 py-0.5 text-[11px]">ENABLE_GMAIL_AUTOMATION</code>
            . Los envíos por email pueden no ejecutarse automáticamente.
          </p>
        </div>
      ) : null}
    </div>
  )
}
