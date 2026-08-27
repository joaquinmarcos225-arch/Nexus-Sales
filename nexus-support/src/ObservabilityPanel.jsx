import { useCallback, useEffect, useState } from 'react'
import { fetchObservability } from './api.js'
import CapacityCalculatorPanel from './CapacityCalculatorPanel.jsx'

const STATUS_STYLE = {
  healthy: 'bg-emerald-100 text-emerald-800 ring-emerald-200',
  degraded: 'bg-amber-100 text-amber-900 ring-amber-200',
  error: 'bg-red-100 text-red-800 ring-red-200',
  offline: 'bg-slate-200 text-slate-700 ring-slate-300',
  stale: 'bg-amber-100 text-amber-900 ring-amber-200',
  running: 'bg-sky-100 text-sky-800 ring-sky-200',
  never: 'bg-slate-200 text-slate-700 ring-slate-300',
}

const STATUS_LABEL = {
  healthy: 'OK',
  degraded: 'Atención',
  error: 'Error',
  offline: 'Sin configurar',
  stale: 'Demorado',
  running: 'Corriendo',
  never: 'Sin ejecución',
}

const LIMIT_LABEL = {
  email: 'Email',
  linkedin_invite: 'LinkedIn invitaciones',
  linkedin_dm: 'LinkedIn mensajes',
  whatsapp: 'WhatsApp',
}

function money(value, digits = 2) {
  const amount = Number(value || 0)
  return `US$ ${amount.toLocaleString('es-AR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

function when(value) {
  if (!value) return 'Nunca'
  try {
    return new Date(value).toLocaleString('es-AR', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return '—'
  }
}

function StatusChip({ status }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${
        STATUS_STYLE[status] || STATUS_STYLE.offline
      }`}
    >
      {STATUS_LABEL[status] || status}
    </span>
  )
}

function Metric({ label, value, detail, alert = false }) {
  return (
    <div className={`rounded-xl border bg-white p-4 shadow-sm ${alert ? 'border-red-300' : 'border-rose-200'}`}>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-rose-600">{label}</p>
      <p className={`mt-1 text-2xl font-bold tabular-nums ${alert ? 'text-red-700' : 'text-rose-950'}`}>{value}</p>
      {detail ? <p className="mt-1 text-xs text-rose-700/75">{detail}</p> : null}
    </div>
  )
}

export default function ObservabilityPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshingProspeo, setRefreshingProspeo] = useState(false)

  const load = useCallback(async (refreshProspeo = false) => {
    if (refreshProspeo) setRefreshingProspeo(true)
    else setLoading(true)
    try {
      setData(await fetchObservability({ refreshProspeo }))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setRefreshingProspeo(false)
    }
  }, [])

  useEffect(() => {
    void load(false)
    const timer = window.setInterval(() => void load(false), 30000)
    return () => window.clearInterval(timer)
  }, [load])

  if (loading && !data) {
    return <div className="p-8 text-center text-sm text-rose-500">Cargando operación…</div>
  }

  const summary = data?.summary || {}
  const scheduler = data?.scheduler || {}

  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-[#fff8f7]">
      <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6">
        {error ? <p className="rounded-xl bg-red-700 px-4 py-2 text-sm text-white">{error}</p> : null}

        <CapacityCalculatorPanel />

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
          <Metric label="Empresas" value={summary.companies ?? 0} detail={`${summary.active_campaigns ?? 0} campañas activas`} />
          <Metric label="Secuencias este mes" value={summary.sequences_started_month ?? 0} />
          <Metric label="Mensajes hoy" value={summary.outbound_messages_today ?? 0} />
          <Metric
            label="Tareas pendientes"
            value={summary.pending_tasks ?? 0}
            detail={`${summary.overdue_tasks ?? 0} vencidas`}
            alert={(summary.overdue_tasks ?? 0) > 0}
          />
          <Metric
            label="Tickets esperando"
            value={data?.support_inbox?.waiting ?? 0}
            detail={`${data?.support_inbox?.open ?? 0} abiertos · ${data?.support_inbox?.resolved ?? 0} resueltos`}
            alert={(data?.support_inbox?.waiting ?? 0) > 0}
          />
          <Metric
            label="Costo estimado mes"
            value={money(summary.estimated_cost_month_usd, 4)}
            detail={`${money(summary.estimated_cost_per_sequence_usd, 3)} / secuencia`}
          />
          <Metric
            label="Scheduler"
            value={scheduler.running ? 'Activo' : 'Detenido'}
            detail={`${scheduler.jobs_with_errors ?? 0} errores · ${scheduler.jobs_stale ?? 0} demorados`}
            alert={!scheduler.running || (scheduler.jobs_with_errors ?? 0) > 0}
          />
        </section>

        <section>
          <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 className="text-sm font-bold text-rose-950">Proveedores y costos</h2>
              <p className="text-xs text-rose-700/70">Costo estimado por uso; presupuesto es el previsto en Ops Cobros.</p>
            </div>
            <button
              type="button"
              disabled={refreshingProspeo}
              onClick={() => void load(true)}
              className="rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-xs font-semibold text-rose-900 hover:bg-rose-50 disabled:opacity-50"
            >
              {refreshingProspeo ? 'Consultando…' : 'Actualizar saldo Prospeo'}
            </button>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            {(data?.providers || []).map((provider) => (
              <article key={provider.key} className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-bold text-rose-950">{provider.label}</h3>
                  <StatusChip status={provider.status} />
                </div>
                <p className="mt-2 min-h-8 text-xs leading-relaxed text-rose-800/75">{provider.detail || '—'}</p>
                <div className="mt-3 border-t border-rose-100 pt-3 text-xs">
                  <div className="flex justify-between gap-2">
                    <span className="text-rose-500">Estimado mes</span>
                    <strong className="tabular-nums text-rose-950">{money(provider.estimated_cost_usd, 4)}</strong>
                  </div>
                  <div className="mt-1 flex justify-between gap-2">
                    <span className="text-rose-500">Presupuesto</span>
                    <strong className="tabular-nums text-rose-950">{money(provider.planned_budget_usd)}</strong>
                  </div>
                </div>
              </article>
            ))}
          </div>
          <p className="mt-2 rounded-lg bg-rose-100/70 px-3 py-2 text-[11px] leading-relaxed text-rose-800">
            {data?.cost_note}
            {data?.billing_cycle ? (
              <>
                {' '}
                Presupuesto Ops {data.billing_cycle.cycle_key}: {money(data.billing_cycle.planned_cogs_usd)} COGS
                planificado ({data.billing_cycle.paid}/{data.billing_cycle.companies_with_cycle} pagaron,
                {' '}
                {data.billing_cycle.credits_granted} acreditados).
              </>
            ) : null}
          </p>
        </section>

        <section className="grid gap-4 xl:grid-cols-2">
          <div className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
            <h2 className="text-sm font-bold text-rose-950">Límites diarios por canal</h2>
            <p className="mt-0.5 text-xs text-rose-700/70">Suma de los topes de SDRs con campañas.</p>
            <div className="mt-3 space-y-3">
              {(data?.channel_limits || []).map((limit) => {
                const pct = limit.limit > 0 ? Math.min(100, Math.round((limit.used / limit.limit) * 100)) : 0
                return (
                  <div key={limit.key}>
                    <div className="flex justify-between text-xs">
                      <span className="font-medium text-rose-900">{LIMIT_LABEL[limit.key] || limit.key}</span>
                      <span className="tabular-nums text-rose-600">{limit.used} / {limit.limit}</span>
                    </div>
                    <div className="mt-1 h-2 overflow-hidden rounded-full bg-rose-100">
                      <div
                        className={`h-full rounded-full ${pct >= 90 ? 'bg-red-600' : pct >= 70 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
            <h2 className="text-sm font-bold text-rose-950">Errores recientes</h2>
            <div className="mt-3 max-h-64 space-y-2 overflow-y-auto">
              {(data?.recent_errors || []).length === 0 ? (
                <p className="rounded-lg bg-emerald-50 px-3 py-4 text-center text-xs text-emerald-800">Sin errores registrados.</p>
              ) : (
                data.recent_errors.map((row, index) => (
                  <div key={`${row.source}-${row.at}-${index}`} className="rounded-lg border border-red-100 bg-red-50 px-3 py-2">
                    <div className="flex justify-between gap-2">
                      <strong className="text-xs text-red-900">{row.label || row.source}</strong>
                      <span className="shrink-0 text-[10px] text-red-500">{when(row.at)}</span>
                    </div>
                    <p className="mt-1 line-clamp-3 text-[11px] leading-relaxed text-red-800">{row.message}</p>
                  </div>
                ))
              )}
            </div>
          </div>
        </section>

        <section className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-bold text-rose-950">Jobs automáticos</h2>
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {(data?.jobs || []).map((job) => (
              <div key={job.job_key} className="rounded-lg border border-rose-100 bg-rose-50/40 px-3 py-2">
                <div className="flex items-start justify-between gap-2">
                  <strong className="text-xs text-rose-950">{job.label}</strong>
                  <StatusChip status={job.status} />
                </div>
                <p className="mt-1 text-[10px] text-rose-500">Último OK: {when(job.last_success_at)} · {job.run_count} ejecuciones</p>
              </div>
            ))}
          </div>
        </section>

        <p className="pb-2 text-right text-[10px] text-rose-400">Actualizado {when(data?.generated_at)} · refresco cada 30 s</p>
      </div>
    </div>
  )
}
