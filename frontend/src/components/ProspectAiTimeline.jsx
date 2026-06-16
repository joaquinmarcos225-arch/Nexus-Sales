import { useCallback, useEffect, useState } from 'react'
import { fetchProspectAiTimeline } from '../utils/api.js'

function fmtTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'medium' })
  } catch {
    return iso
  }
}

const TYPE_COLORS = {
  inbound_classify: 'bg-violet-50 text-violet-800',
  inbound_compose: 'bg-sky-50 text-sky-800',
  inbound_deliver: 'bg-emerald-50 text-emerald-800',
  inbound_skip: 'bg-amber-50 text-amber-800',
  ops_control: 'bg-slate-100 text-slate-700',
}

export function ProspectAiTimeline({ companyId, prospectId, prospectName }) {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!companyId || !prospectId) {
      setEvents([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await fetchProspectAiTimeline(companyId, prospectId, 50)
      setEvents(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setEvents([])
    } finally {
      setLoading(false)
    }
  }, [companyId, prospectId])

  useEffect(() => {
    void load()
  }, [load])

  if (!prospectId) return null

  return (
    <section className="mt-4 rounded-xl border border-[#e5e7eb] bg-[#fafbfc] p-4">
      <TimelineHeader prospectName={prospectName} loading={loading} onRefresh={load} />
      {error ? <p className="mt-2 text-xs text-rose-600">{error}</p> : null}
      {loading && !events.length ? (
        <p className="mt-3 text-xs text-[#6b7280]">Cargando timeline IA…</p>
      ) : null}
      {!loading && events.length === 0 ? (
        <p className="mt-3 text-xs text-[#9ca3af]">Sin decisiones registradas para este prospecto.</p>
      ) : (
        <ol className="relative mt-4 space-y-0 border-l border-[#e5e7eb] pl-4">
          {events.map((ev) => (
            <li key={ev.id} className="relative pb-4 pl-4 last:pb-0">
              <span className="absolute -left-[1.35rem] top-1 h-2.5 w-2.5 rounded-full bg-nx-brand ring-2 ring-white" />
              <time className="text-[10px] text-[#9ca3af]">{fmtTime(ev.at)}</time>
              <p className="mt-0.5 text-sm font-medium text-[#111827]">{ev.summary}</p>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-[#6b7280]">
                <span
                  className={`rounded px-1.5 py-0.5 font-mono font-semibold ${
                    TYPE_COLORS[ev.event_type] || 'bg-[#f1f5f9] text-[#475569]'
                  }`}
                >
                  {ev.event_type}
                </span>
                <span>{ev.decision}</span>
                {ev.confidence != null ? (
                  <span>conf. {(ev.confidence * 100).toFixed(0)}%</span>
                ) : null}
              </p>
              {ev.payload?.signals ? (
                <details className="mt-2 text-[11px] text-[#6b7280]">
                  <summary className="cursor-pointer font-medium text-[#374151]">Señales IA</summary>
                  <pre className="mt-1 max-h-32 overflow-auto rounded bg-white p-2 text-[10px]">
                    {JSON.stringify(ev.payload.signals, null, 2)}
                  </pre>
                </details>
              ) : null}
              {ev.payload && !ev.payload.signals ? (
                <details className="mt-1 text-[11px]">
                  <summary className="cursor-pointer text-[#6b7280]">Detalle</summary>
                  <pre className="mt-1 max-h-24 overflow-auto rounded bg-white p-2 text-[10px] text-[#475569]">
                    {JSON.stringify(ev.payload, null, 2)}
                  </pre>
                </details>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function TimelineHeader({ prospectName, loading, onRefresh }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h3 className="text-sm font-semibold text-[#111827]">Timeline IA</h3>
        {prospectName ? (
          <p className="text-xs text-[#6b7280]">{prospectName} — debug mental del agente</p>
        ) : null}
      </div>
      <button
        type="button"
        disabled={loading}
        onClick={() => void onRefresh()}
        className="rounded-md border border-[#e5e7eb] bg-white px-2 py-1 text-[11px] font-semibold text-[#374151] hover:bg-white/80"
      >
        Actualizar
      </button>
    </div>
  )
}
