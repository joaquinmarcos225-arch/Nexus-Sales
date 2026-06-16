import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchCompanyOutreachTasks } from '../../utils/api.js'

function labelForKind(task) {
  const fromApi = (task?.action_label || '').trim()
  if (fromApi) {
    return fromApi
  }
  return 'Tarea'
}

export function RecommendedActionsSection({
  companyId,
  campaignId = null,
  title = 'Acciones recomendadas por Nexus',
  reloadKey = 0,
}) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!companyId) {
      setTasks([])
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    void fetchCompanyOutreachTasks(companyId, {
      status: 'pending',
      campaignId: campaignId ?? undefined,
    })
      .then((list) => {
        if (!cancelled) {
          setTasks(Array.isArray(list) ? list : [])
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setTasks([])
          setError(e instanceof Error ? e.message : String(e))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [companyId, campaignId, reloadKey])

  const grouped = useMemo(() => {
    const m = new Map()
    for (const t of tasks) {
      const k = labelForKind(t)
      if (!m.has(k)) {
        m.set(k, [])
      }
      m.get(k).push(t)
    }
    return m
  }, [tasks])

  if (!companyId) {
    return null
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {campaignId != null ? (
          <span className="text-[11px] text-slate-500">Solo esta campaña</span>
        ) : (
          <span className="text-[11px] text-slate-500">Toda la empresa</span>
        )}
      </div>
      {error ? (
        <p className="mt-2 text-xs text-rose-700">{error}</p>
      ) : null}
      {loading ? (
        <p className="mt-3 text-xs text-slate-500">Cargando tareas…</p>
      ) : tasks.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          No hay tareas pendientes. Ejecutá Autopilot o outreach para generar seguimiento y alertas.
        </p>
      ) : (
        <div className="mt-3 space-y-4">
          {[...grouped.entries()].map(([kindLabel, rows]) => (
            <div key={kindLabel}>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                {kindLabel}
              </p>
              <ul className="mt-1.5 space-y-2 text-sm text-slate-700">
                {rows.slice(0, 8).map((task) => (
                  <li
                    key={task.id}
                    className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-slate-100 bg-slate-50/80 px-2 py-1.5"
                  >
                    <div className="min-w-0 max-w-[42rem]">
                      <p className="font-semibold text-slate-900">
                        {(task.headline || '').trim() || task.title}
                      </p>
                      <p className="mt-1 text-xs text-slate-600">
                        <span className="font-medium text-slate-700">Campaña:</span>{' '}
                        {(task.campaign_name || '').trim() || '—'}
                        {' · '}
                        <span className="font-medium text-slate-700">Prospecto:</span>{' '}
                        {(task.prospect_name || '').trim() || '—'}
                        {(task.prospect_company || '').trim()
                          ? ` (${(task.prospect_company || '').trim()})`
                          : ''}
                      </p>
                      {(task.reason || '').trim() ? (
                        <p className="mt-1 text-xs text-slate-600">
                          <span className="font-medium text-slate-700">Motivo:</span> {task.reason}
                        </p>
                      ) : null}
                      {(task.suggested_action || '').trim() ? (
                        <p className="mt-1 text-xs text-slate-600">
                          <span className="font-medium text-slate-700">Acción sugerida:</span>{' '}
                          {task.suggested_action}
                        </p>
                      ) : null}
                    </div>
                    <Link
                      to={`/campanas/${task.campaign_id}`}
                      className="shrink-0 text-xs font-semibold text-sky-700 hover:text-sky-900"
                    >
                      Abrir campaña
                    </Link>
                  </li>
                ))}
              </ul>
              {rows.length > 8 ? (
                <p className="mt-1 text-[11px] text-slate-400">+{rows.length - 8} más…</p>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
