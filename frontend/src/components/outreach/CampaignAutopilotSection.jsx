import { useState } from 'react'
import { AlertBanner } from '../AlertBanner.jsx'
import {
  activateCampaignAutopilot,
  pauseCampaignAutopilot,
  runCampaignAutopilotCycle,
} from '../../utils/api.js'

const LABELS = {
  off: 'Off',
  running: 'Running',
  paused: 'Paused',
  completed: 'Completed',
}

function Metric({ label, value }) {
  return (
    <div className="rounded-lg border border-[#e5e7eb] bg-[#f8fafc] px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-[#6b7280]">{label}</p>
      <p className="text-lg font-semibold text-[#111827] tabular-nums">{value ?? 0}</p>
    </div>
  )
}

export function CampaignAutopilotSection({ campaign, freeze = false, onChanged }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)
  const [lastCycle, setLastCycle] = useState(null)

  const status = campaign?.autopilot_status ?? 'off'
  const summary = campaign?.autopilot_last_cycle_summary || {}
  const stats = lastCycle?.stats || summary
  const log = Array.isArray(lastCycle?.log) ? lastCycle.log : Array.isArray(summary.log) ? summary.log : []

  async function handleActivate() {
    setBusy('activate')
    setError(null)
    try {
      await activateCampaignAutopilot(campaign.id)
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  async function handlePause() {
    setBusy('pause')
    setError(null)
    try {
      await pauseCampaignAutopilot(campaign.id)
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  async function handleRunCycle() {
    setBusy('cycle')
    setError(null)
    try {
      const res = await runCampaignAutopilotCycle(campaign.id)
      setLastCycle(res)
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <section className="rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm shadow-[#111827]/5 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[#111827]">Autopilot Nexus</h2>
          <p className="mt-1 text-xs text-[#6b7280]">
            Flujo autónomo: contacta, simula respuestas, analiza, actualiza pipeline, crea follow-ups, tareas y reuniones.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void handleActivate()}
            disabled={freeze || busy !== '' || !campaign}
            className="rounded-lg bg-nx-brand px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-40"
          >
            {busy === 'activate' ? 'Activando…' : 'Activar Autopilot'}
          </button>
          <button
            type="button"
            onClick={() => void handlePause()}
            disabled={freeze || busy !== '' || !campaign}
            className="rounded-lg border border-[#e5e7eb] bg-white px-3 py-2 text-xs font-semibold text-[#374151] hover:bg-[#f8fafc] disabled:opacity-40"
          >
            {busy === 'pause' ? 'Pausando…' : 'Pausar Autopilot'}
          </button>
          <button
            type="button"
            onClick={() => void handleRunCycle()}
            disabled={freeze || busy !== '' || !campaign}
            className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-900 hover:bg-emerald-100 disabled:opacity-40"
          >
            {busy === 'cycle' ? 'Ejecutando…' : 'Ejecutar siguiente ciclo ahora'}
          </button>
        </div>
      </div>

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <div className="text-xs text-[#6b7280]">
        Estado: <span className="font-semibold text-[#111827]">{LABELS[status] ?? status}</span> · Último ciclo:{' '}
        {campaign?.autopilot_last_cycle_at
          ? new Date(campaign.autopilot_last_cycle_at).toLocaleString('es-AR')
          : '—'}
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
        <Metric label="Prospectos procesados" value={stats.processed} />
        <Metric label="Mensajes generados" value={stats.messages_generated} />
        <Metric label="Respuestas simuladas" value={stats.responses_simulated} />
        <Metric label="Follow-ups creados" value={stats.followups_generated} />
        <Metric label="Tareas creadas" value={stats.tasks_created} />
        <Metric label="Reuniones creadas" value={stats.meetings_created} />
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Log del ciclo</p>
        {log.length ? (
          <ul className="mt-2 space-y-1 text-xs text-slate-700">
            {log.slice(0, 6).map((item, i) => (
              <li key={`${item}-${i}`}>• {item}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-xs text-slate-500">Sin actividad registrada todavía.</p>
        )}
      </div>
    </section>
  )
}
