import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { PageHeader } from '../layout/PageHeader'
import {
  fetchOperationsActivityFeed,
  fetchOperationsOverview,
  postEmergencyStop,
  patchCampaignAutomationMode,
} from '../utils/api.js'

function StatusPill({ ok, label, warn }) {
  const cls = ok
    ? 'bg-emerald-50 text-emerald-800 ring-emerald-200'
    : warn
      ? 'bg-amber-50 text-amber-800 ring-amber-200'
      : 'bg-rose-50 text-rose-800 ring-rose-200'
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ${cls}`}>
      {label}
    </span>
  )
}

function MetricCard({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-[#6b7280]">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-[#111827]">{value}</p>
      {sub ? <p className="mt-1 text-xs text-[#9ca3af]">{sub}</p> : null}
    </div>
  )
}

function Panel({ title, hint, children, className = '' }) {
  return (
    <section className={`rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm ${className}`}>
      <h2 className="text-sm font-semibold text-[#111827]">{title}</h2>
      {hint ? <p className="mt-0.5 text-xs text-[#6b7280]">{hint}</p> : null}
      {children}
    </section>
  )
}

function fmtTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'medium' })
  } catch {
    return iso
  }
}

function pct(n) {
  if (n == null || Number.isNaN(n)) return '0%'
  return `${(Number(n) * 100).toFixed(1)}%`
}

const MODE_LABELS = {
  manual: 'Manual',
  semi_auto: 'Semi-auto',
  full_auto: 'Full-auto',
}

export default function OperacionesPage() {
  const { companyId, loading: ctxLoading } = useCompany()
  const [overview, setOverview] = useState(null)
  const [feed, setFeed] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [savingStop, setSavingStop] = useState(false)
  const [modeSaving, setModeSaving] = useState(null)

  const load = useCallback(async () => {
    if (!companyId) {
      setOverview(null)
      setFeed([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [ov, fd] = await Promise.all([
        fetchOperationsOverview(companyId),
        fetchOperationsActivityFeed(companyId, 40),
      ])
      setOverview(ov)
      setFeed(Array.isArray(fd) ? fd : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void load()
    const t = setInterval(() => void load(), 45_000)
    return () => clearInterval(t)
  }, [load])

  async function toggleEmergencyStop() {
    if (!companyId || !overview) return
    setSavingStop(true)
    try {
      await postEmergencyStop(companyId, !overview.global_automation_stop)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSavingStop(false)
    }
  }

  async function setCampaignMode(campaignId, mode) {
    if (!companyId) return
    setModeSaving(campaignId)
    try {
      await patchCampaignAutomationMode(companyId, campaignId, mode)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setModeSaving(null)
    }
  }

  const m = overview?.metrics_24h
  const sched = overview?.scheduler
  const integ = overview?.integrations
  const tasks = overview?.inbound_auto_reply_tasks || {}
  const queue = overview?.task_queue

  return (
    <>
      <PageHeader
        title="Centro de Operaciones"
        description="Observabilidad y control del motor autónomo de SDRs IA — jobs, colas, salud y decisiones."
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <div className="-mx-2 max-w-[1400px] lg:-mx-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <StatusPills overview={overview} sched={sched} integ={integ} />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={loading || !companyId}
              onClick={() => void load()}
              className="rounded-lg border border-[#e5e7eb] bg-white px-3 py-2 text-xs font-semibold text-[#374151] hover:bg-[#f8fafc]"
            >
              Actualizar
            </button>
            <button
              type="button"
              disabled={savingStop || !overview}
              onClick={() => void toggleEmergencyStop()}
              className={`rounded-lg px-3 py-2 text-xs font-semibold text-white shadow-sm disabled:opacity-50 ${
                overview?.global_automation_stop
                  ? 'bg-emerald-600 hover:bg-emerald-700'
                  : 'bg-rose-600 hover:bg-rose-700'
              }`}
            >
              {savingStop
                ? '…'
                : overview?.global_automation_stop
                  ? 'Reanudar todo'
                  : 'Parada de emergencia'}
            </button>
          </div>
        </div>

        {(loading || ctxLoading) && !overview ? (
          <p className="text-sm text-[#6b7280]">Cargando centro de operaciones…</p>
        ) : null}

        {overview ? (
          <>
            <section className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
              <MetricCard label="Emails enviados (24h)" value={m?.emails_sent ?? 0} />
              <MetricCard label="Inbound detectados" value={m?.inbound_detected ?? 0} />
              <MetricCard label="Auto-respuestas" value={m?.auto_replies_sent ?? 0} sub="enviadas" />
              <MetricCard label="Borradores IA" value={m?.auto_replies_draft ?? 0} />
              <MetricCard label="Reply rate" value={pct(m?.reply_rate)} />
              <MetricCard label="Reuniones (24h)" value={m?.meetings_booked ?? 0} />
            </section>

            <div className="mb-6 grid gap-4 lg:grid-cols-3">
              <Panel title="Jobs del scheduler" hint="Última ejecución, duración y errores" className="lg:col-span-2">
                <JobsTable jobs={overview.jobs || []} />
              </Panel>
              <Panel title="Colas y salud">
                <HealthDl integ={integ} tasks={tasks} queue={queue} m={m} />
                <RecentErrors errors={overview.recent_errors} />
              </Panel>
            </div>

            <Panel title="Control por campaña" hint="Modo manual, semi-auto o full-auto" className="mb-6">
              <CampaignModeTable
                campaigns={overview.campaigns || []}
                modeSaving={modeSaving}
                onMode={setCampaignMode}
              />
            </Panel>

            <Panel title="Timeline de decisiones IA" hint="Clasificaciones, entregas y controles">
              <DecisionFeed feed={feed} />
            </Panel>
          </>
        ) : null}
      </div>
    </>
  )
}

function StatusPills({ overview, sched, integ }) {
  if (!overview) return null
  return (
    <div className="flex flex-wrap gap-2">
      <StatusPill
        ok={sched?.running}
        warn={!sched?.running && sched?.enabled_env}
        label={sched?.running ? 'Scheduler activo' : 'Scheduler detenido'}
      />
      <StatusPill
        ok={integ?.status === 'healthy'}
        warn={integ?.status === 'degraded'}
        label={`Integraciones · ${integ?.status || '—'}`}
      />
      <StatusPill
        ok={!overview.global_automation_stop}
        label={overview.global_automation_stop ? 'Parada global ON' : 'Automatización global ON'}
      />
    </div>
  )
}

function JobsTable({ jobs }) {
  return (
    <div className="mt-3 overflow-x-auto rounded-lg border border-[#e5e7eb]">
      <table className="min-w-[640px] w-full text-left text-xs">
        <thead className="bg-[#f8fafc] text-[#6b7280]">
          <tr>
            <th className="px-3 py-2 font-semibold">Job</th>
            <th className="px-3 py-2 font-semibold">Último OK</th>
            <th className="px-3 py-2 font-semibold">Duración</th>
            <th className="px-3 py-2 font-semibold">Runs</th>
            <th className="px-3 py-2 font-semibold">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#e5e7eb]">
          {jobs.map((j) => (
            <tr key={j.job_key} className="text-[#374151]">
              <td className="px-3 py-2 font-medium">{j.label || j.job_key}</td>
              <td className="whitespace-nowrap px-3 py-2">{fmtTime(j.last_success_at)}</td>
              <td className="px-3 py-2 tabular-nums">
                {j.duration_sec != null ? `${j.duration_sec.toFixed(1)}s` : '—'}
              </td>
              <td className="px-3 py-2 tabular-nums">{j.run_count ?? 0}</td>
              <td className="max-w-[12rem] truncate px-3 py-2 text-rose-600" title={j.last_error || ''}>
                {j.last_error || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function HealthDl({ integ, tasks, queue, m }) {
  return (
    <dl className="mt-3 space-y-3 text-xs">
      <Row label="Gmail conectado" value={`${integ?.gmail_connected ?? 0} cuenta(s)`} />
      <Row label="Calendar conectado" value={`${integ?.calendar_connected ?? 0}`} />
      <Row label="Auto-reply pendientes" value={tasks.pending ?? 0} />
      <Row label="Tareas pendientes" value={queue?.total_pending ?? 0} />
      <Row label="Vencen en 15m" value={queue?.due_within_15m ?? 0} />
      <Row label="Omitidos (24h)" value={m?.auto_replies_skipped ?? 0} />
    </dl>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-[#6b7280]">{label}</dt>
      <dd className="font-semibold tabular-nums text-[#111827]">{value}</dd>
    </div>
  )
}

function RecentErrors({ errors }) {
  if (!errors?.length) return null
  return (
    <div className="mt-4 rounded-lg border border-rose-100 bg-rose-50/80 p-3">
      <p className="text-[11px] font-semibold text-rose-800">Errores recientes</p>
      <ul className="mt-1 space-y-1 text-[11px] text-rose-700">
        {errors.map((e) => (
          <li key={e.job_key}>
            {e.label}: {e.error}
          </li>
        ))}
      </ul>
    </div>
  )
}

function CampaignModeTable({ campaigns, modeSaving, onMode }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="min-w-[720px] w-full text-left text-xs">
        <thead className="bg-[#f8fafc] text-[#6b7280]">
          <tr>
            <th className="px-3 py-2 font-semibold">Campaña</th>
            <th className="px-3 py-2 font-semibold">Estado</th>
            <th className="px-3 py-2 font-semibold">Modo</th>
            <th className="px-3 py-2 font-semibold">Acciones</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#e5e7eb]">
          {campaigns.map((c) => (
            <tr key={c.id}>
              <td className="px-3 py-2 font-medium text-[#111827]">
                <Link to={`/campanas/${c.id}`} className="hover:text-nx-brand">
                  {c.name}
                </Link>
              </td>
              <td className="px-3 py-2">
                {c.automation_paused ? (
                  <span className="text-amber-700">Pausada</span>
                ) : (
                  <span className="text-emerald-700">{c.status}</span>
                )}
              </td>
              <td className="px-3 py-2">{MODE_LABELS[c.automation_mode] || c.automation_mode}</td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {['manual', 'semi_auto', 'full_auto'].map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      disabled={modeSaving === c.id}
                      onClick={() => void onMode(c.id, mode)}
                      className={`rounded-md px-2 py-1 text-[10px] font-semibold ring-1 ${
                        c.automation_mode === mode
                          ? 'bg-nx-brand text-white ring-nx-brand'
                          : 'bg-white text-[#374151] ring-[#e5e7eb] hover:bg-[#f8fafc]'
                      }`}
                    >
                      {MODE_LABELS[mode]}
                    </button>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DecisionFeed({ feed }) {
  return (
    <ul className="mt-4 divide-y divide-[#e5e7eb]">
      {feed.length === 0 ? (
        <li className="py-6 text-center text-sm text-[#9ca3af]">
          Sin eventos todavía. Procesá un inbound para ver decisiones.
        </li>
      ) : (
        feed.map((ev) => (
          <li key={ev.id} className="flex gap-3 py-3 text-xs">
            <time className="w-28 shrink-0 text-[#9ca3af]">{fmtTime(ev.at)}</time>
            <div className="min-w-0 flex-1">
              <p className="font-medium text-[#111827]">{ev.summary}</p>
              <p className="mt-0.5 text-[#6b7280]">
                <span className="rounded bg-[#f1f5f9] px-1.5 py-0.5 font-mono text-[10px]">
                  {ev.event_type}
                </span>
                <span className="ml-2">{ev.decision}</span>
                {ev.campaign_id ? (
                  <Link className="ml-2 text-nx-brand hover:underline" to={`/campanas/${ev.campaign_id}`}>
                    campaña #{ev.campaign_id}
                  </Link>
                ) : null}
              </p>
            </div>
          </li>
        ))
      )}
    </ul>
  )
}
