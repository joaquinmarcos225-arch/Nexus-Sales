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
    ? 'bg-red-50 text-red-800 ring-red-200'
    : warn
      ? 'bg-zinc-50 text-zinc-800 ring-zinc-200'
      : 'bg-red-50 text-red-800 ring-red-200'
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ${cls}`}>
      {label}
    </span>
  )
}

function MetricCard({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-nx-border bg-white p-4 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-nx-ink">{value}</p>
      {sub ? <p className="mt-1 text-xs text-nx-subtle">{sub}</p> : null}
    </div>
  )
}

function Panel({ title, hint, children, className = '' }) {
  return (
    <section className={`rounded-xl border border-nx-border bg-white p-4 shadow-sm ${className}`}>
      <h2 className="text-sm font-semibold text-nx-ink">{title}</h2>
      {hint ? <p className="mt-0.5 text-xs text-nx-muted">{hint}</p> : null}
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

const CAMPAIGN_STATUS_LABELS = {
  draft: 'Borrador',
  ready: 'Lista',
  running: 'En curso',
  paused: 'Pausada',
  completed: 'Completada',
}

function campaignStatusLabel(c) {
  if (c?.automation_paused) {
    return 'Pausada'
  }
  return CAMPAIGN_STATUS_LABELS[c?.status] || c?.status || '—'
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
        actions={
          <Link
            to="/configuracion/integraciones"
            className="text-xs font-semibold text-nx-brand hover:underline"
          >
            Configuración →
          </Link>
        }
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
              className="rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted"
            >
              Actualizar
            </button>
            <button
              type="button"
              disabled={savingStop || !overview}
              onClick={() => void toggleEmergencyStop()}
              className={`rounded-lg px-3 py-2 text-xs font-semibold text-white shadow-sm disabled:opacity-50 ${
                overview?.global_automation_stop
                  ? 'bg-red-600 hover:bg-red-700'
                  : 'bg-red-600 hover:bg-red-700'
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
          <p className="text-sm text-nx-muted">Cargando centro de operaciones…</p>
        ) : null}

        {overview && !sched?.running ? (
          <div className="mb-4 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 text-xs text-zinc-900">
            <p className="font-semibold">Scheduler detenido en este servidor</p>
            <p className="mt-1 text-zinc-800">
              Para jobs automáticos (Gmail inbound, follow-ups, auto-respuesta), activá en{' '}
              <code className="rounded bg-white/80 px-1">backend/.env</code>:{' '}
              <code className="rounded bg-white/80 px-1">NEXUS_AUTOMATION_SCHEDULER=1</code>
              {sched?.enabled_env ? ` (env actual: "${sched.enabled_env}")` : ' (variable vacía o ausente)'}.
              Reiniciá uvicorn después de cambiarla.
            </p>
          </div>
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
  const waOk = integ?.whatsapp_configured
  const waDry = integ?.whatsapp_dry_run
  return (
    <div className="flex flex-wrap gap-2">
      <StatusPill
        ok={overview.real_mode}
        warn={!overview.real_mode}
        label={overview.real_mode ? 'Modo real' : 'Modo simulación'}
      />
      <StatusPill
        ok={sched?.running}
        warn={!sched?.running && sched?.enabled_env}
        label={sched?.running ? 'Scheduler activo' : 'Scheduler detenido'}
      />
      <StatusPill
        ok={Boolean(integ?.gmail_connected)}
        warn={!integ?.gmail_connected}
        label={integ?.gmail_connected ? 'Gmail OK' : 'Gmail pendiente'}
      />
      <StatusPill
        ok={Boolean(integ?.calendar_connected)}
        warn={!integ?.calendar_connected}
        label={integ?.calendar_connected ? 'Calendar OK' : 'Calendar pendiente'}
      />
      <StatusPill
        ok={waOk && !waDry}
        warn={waDry || !waOk}
        label={
          waDry
            ? 'WhatsApp dry-run (apagar)'
            : waOk
              ? 'WhatsApp Web asistido'
              : 'WhatsApp (extensión)'
        }
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
    <div className="mt-3 overflow-x-auto rounded-lg border border-nx-border">
      <table className="min-w-[640px] w-full text-left text-xs">
        <thead className="bg-nx-card-muted text-nx-muted">
          <tr>
            <th className="px-3 py-2 font-semibold">Job</th>
            <th className="px-3 py-2 font-semibold">Último OK</th>
            <th className="px-3 py-2 font-semibold">Duración</th>
            <th className="px-3 py-2 font-semibold">Runs</th>
            <th className="px-3 py-2 font-semibold">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-nx-border">
          {jobs.map((j) => (
            <tr key={j.job_key} className="text-nx-ink">
              <td className="px-3 py-2 font-medium">{j.label || j.job_key}</td>
              <td className="whitespace-nowrap px-3 py-2">{fmtTime(j.last_success_at)}</td>
              <td className="px-3 py-2 tabular-nums">
                {j.duration_sec != null ? `${j.duration_sec.toFixed(1)}s` : '—'}
              </td>
              <td className="px-3 py-2 tabular-nums">{j.run_count ?? 0}</td>
              <td className="max-w-[12rem] truncate px-3 py-2 text-red-600" title={j.last_error || ''}>
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
      <Row
        label="WhatsApp"
        value={
          integ?.whatsapp_dry_run
            ? 'Dry-run (apagar)'
            : integ?.whatsapp_configured
              ? 'Cloud API opt-in'
              : 'Web asistido (extensión)'
        }
      />
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
      <dt className="text-nx-muted">{label}</dt>
      <dd className="font-semibold tabular-nums text-nx-ink">{value}</dd>
    </div>
  )
}

function RecentErrors({ errors }) {
  if (!errors?.length) return null
  return (
    <div className="mt-4 rounded-lg border border-red-100 bg-red-50/80 p-3">
      <p className="text-[11px] font-semibold text-red-800">Errores recientes</p>
      <ul className="mt-1 space-y-1 text-[11px] text-red-700">
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
  if (!campaigns?.length) {
    return (
      <p className="mt-3 text-xs text-nx-muted">
        No hay campañas en esta empresa. Creá una en{' '}
        <Link to="/campanas" className="font-semibold text-nx-brand hover:underline">
          Campañas
        </Link>
        .
      </p>
    )
  }
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="min-w-[720px] w-full text-left text-xs">
        <thead className="bg-nx-card-muted text-nx-muted">
          <tr>
            <th className="px-3 py-2 font-semibold">Campaña</th>
            <th className="px-3 py-2 font-semibold">Estado</th>
            <th className="px-3 py-2 font-semibold">Modo</th>
            <th className="px-3 py-2 font-semibold">Acciones</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-nx-border">
          {campaigns.map((c) => (
            <tr key={c.id}>
              <td className="px-3 py-2 font-medium text-nx-ink">
                <Link to={`/campanas/${c.id}`} className="hover:text-nx-brand">
                  {c.name}
                </Link>
              </td>
              <td className="px-3 py-2">
                <span className={c.automation_paused ? 'text-zinc-700' : 'text-red-700'}>
                  {campaignStatusLabel(c)}
                </span>
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
                          : 'bg-white text-nx-ink ring-nx-border hover:bg-nx-card-muted'
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
    <ul className="mt-4 divide-y divide-nx-border">
      {feed.length === 0 ? (
        <li className="py-6 text-center text-sm text-nx-subtle">
          Sin eventos todavía. Procesá un inbound para ver decisiones.
        </li>
      ) : (
        feed.map((ev) => (
          <li key={ev.id} className="flex gap-3 py-3 text-xs">
            <time className="w-28 shrink-0 text-nx-subtle">{fmtTime(ev.at)}</time>
            <div className="min-w-0 flex-1">
              <p className="font-medium text-nx-ink">{ev.summary}</p>
              <p className="mt-0.5 text-nx-muted">
                <span className="rounded bg-nx-card-muted px-1.5 py-0.5 font-mono text-[10px]">
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
