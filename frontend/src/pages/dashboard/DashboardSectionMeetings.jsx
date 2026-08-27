import { useEffect, useState } from 'react'
import { PageHeader } from '../../layout/PageHeader'
import { useCompany } from '../../context/CompanyContext.jsx'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'
import { fetchCompanyMeetings } from '../../utils/api.js'
import { fmtDateTime } from '../../utils/ownershipUi.js'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { NX_CHART, NX_CHART_GRID, NX_CHART_TOOLTIP, averageBy, chartAvgCaption } from '../../utils/chartTheme.js'

const MEETING_STATUS_LABELS = {
  pending: 'Invitación enviada',
  confirmed: 'Confirmada',
  completed: 'Completada',
  canceled: 'Cancelada',
  no_show: 'No asistió',
}

const COMMERCIAL_STATUS_LABELS = {
  no_interesado: 'No interesado',
  no_prioridad: 'Contactar más adelante',
}

function meetingStatusLabel(meeting) {
  const commercial = String(meeting?.prospect_commercial_state || '').toLowerCase()
  const status = String(meeting?.meeting_status || '').toLowerCase()

  if (commercial === 'no_interesado' && status !== 'completed') {
    return COMMERCIAL_STATUS_LABELS.no_interesado
  }
  if (commercial === 'no_prioridad' && status === 'pending') {
    return COMMERCIAL_STATUS_LABELS.no_prioridad
  }
  if (status === 'pending' && !meeting?.google_calendar_event_id) {
    return 'Pendiente de agendar'
  }
  return MEETING_STATUS_LABELS[status] || status || '—'
}

export default function DashboardSectionMeetings() {
  const { companyId } = useCompany()
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const weekly = data?.weekly_meetings ?? []
  const commercial = data?.commercial
  const campaigns = (data?.campaigns ?? []).map((c) => ({ ...c, id: c.campaign_id }))
  const meetingRows = [...campaigns]
    .filter((c) => (c.meetings_scheduled ?? 0) > 0 || c.meetings > 0)
    .sort((a, b) => (b.meetings_scheduled ?? 0) - (a.meetings_scheduled ?? 0))

  const [meetings, setMeetings] = useState([])
  const [showCanceled, setShowCanceled] = useState(false)

  useEffect(() => {
    if (!companyId) {
      setMeetings([])
      return
    }
    let c = false
    void fetchCompanyMeetings(companyId, { includeCanceled: showCanceled })
      .then((list) => {
        if (!c) setMeetings(Array.isArray(list) ? list : [])
      })
      .catch(() => {
        if (!c) setMeetings([])
      })
    return () => {
      c = true
    }
  }, [companyId, data, showCanceled])

  const avgWeekly = averageBy(weekly, 'count')

  return (
    <>
      <PageHeader
        title="Reuniones agendadas"
        description="Reuniones creadas por Nexus (auto-booking o manual). Incluye invitaciones de Google Calendar."
      />
      <AlertBanner message={error} onDismiss={() => void refresh()} />

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando…</p>
      ) : (
        <>
          {commercial ? (
            <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <div className="rounded-xl border border-nx-border bg-nx-card p-3 shadow-sm">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Pendientes</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{commercial.meetings_pending}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3 shadow-sm">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Confirmadas</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{commercial.meetings_confirmed}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3 shadow-sm">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Completadas</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{commercial.meetings_completed}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3 shadow-sm">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Cancel / No-show</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">
                  {commercial.meetings_canceled + commercial.meetings_no_show}
                </p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3 shadow-sm">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Tasa completitud</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">
                  {(Number(commercial.meeting_completion_rate || 0) * 100).toFixed(1)}%
                </p>
              </div>
            </div>
          ) : null}

          <div className="h-64 w-full rounded-xl border border-nx-border bg-nx-card p-4">
            <p className="mb-0.5 text-xs font-semibold text-nx-muted">
              Reuniones completadas por semana (fecha agendada)
            </p>
            {weekly.length ? (
              <p className="mb-2 text-[10px] text-nx-muted">{chartAvgCaption('Prom. semanal', avgWeekly)}</p>
            ) : (
              <p className="mb-2 text-[10px] text-nx-muted">Sin datos semanales aún.</p>
            )}
            <ResponsiveContainer width="100%" height="85%">
              <LineChart data={weekly} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...NX_CHART_GRID} />
                <XAxis dataKey="week_label" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip {...NX_CHART_TOOLTIP} />
                <Line
                  type="monotone"
                  dataKey="count"
                  stroke={NX_CHART.brand}
                  strokeWidth={2}
                  name="Completadas"
                  dot={{ fill: NX_CHART.brandDeep }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4 h-56 w-full rounded-xl border border-nx-border bg-nx-card p-4">
            <p className="mb-2 text-xs font-semibold text-nx-muted">Top campañas por reuniones (módulo)</p>
            <ResponsiveContainer width="100%" height="82%">
              <BarChart
                data={meetingRows.slice(0, 8).map((c) => ({
                  name: c.name.length > 16 ? `${c.name.slice(0, 15)}…` : c.name,
                  reuniones: c.meetings_scheduled ?? 0,
                }))}
                margin={{ top: 4, right: 8, left: 0, bottom: 40 }}
              >
                <CartesianGrid {...NX_CHART_GRID} />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-20} textAnchor="end" height={48} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="reuniones" fill={NX_CHART.brandHover} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-6">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-semibold text-nx-muted">Reuniones agendadas (recientes)</p>
              <label className="flex items-center gap-2 text-xs text-nx-muted">
                <input
                  type="checkbox"
                  checked={showCanceled}
                  onChange={(e) => setShowCanceled(e.target.checked)}
                  className="rounded border-nx-border"
                />
                Mostrar canceladas / rechazadas
              </label>
            </div>
            <div className="overflow-x-auto rounded-xl border border-nx-border bg-nx-card">
              <table className="w-full min-w-[920px] text-left text-sm">
                <thead className="border-b border-nx-border bg-nx-card-muted/40 text-[11px] uppercase text-nx-muted">
                  <tr>
                    <th className="px-3 py-2 font-semibold">Prospecto</th>
                    <th className="px-3 py-2 font-semibold">Campaña</th>
                    <th className="px-3 py-2 font-semibold">Cuándo</th>
                    <th className="px-3 py-2 font-semibold">Estado</th>
                    <th className="px-3 py-2 font-semibold">Calendar</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-nx-border">
                  {meetings.slice(0, 30).map((m) => (
                    <tr key={m.id} className="hover:bg-nx-card-muted/30">
                      <td className="px-3 py-2 text-nx-ink">
                        <p className="font-medium">{m.prospect_name || '—'}</p>
                        <p className="text-xs text-nx-muted">{m.prospect_company_name || m.title}</p>
                      </td>
                      <td className="max-w-[160px] truncate px-3 py-2 text-xs text-nx-muted" title={m.campaign_name}>
                        {m.campaign_name || `#${m.campaign_id}`}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-nx-muted">
                        {fmtDateTime(m.scheduled_for, m.timezone)}
                      </td>
                      <td className="px-3 py-2 text-xs text-nx-ink">{meetingStatusLabel(m)}</td>
                      <td className="px-3 py-2 text-xs">
                        {m.google_calendar_html_link ? (
                          <a
                            href={m.google_calendar_html_link}
                            target="_blank"
                            rel="noreferrer"
                            className="font-medium text-zinc-700 hover:underline"
                          >
                            Ver invitación
                          </a>
                        ) : (
                          <span className="text-nx-muted">Sin evento</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!meetings.length ? (
                <p className="px-3 py-8 text-center text-sm text-nx-muted">
                  Sin reuniones todavía. Cuando un prospecto confirme horario, aparecerá acá.
                </p>
              ) : null}
            </div>
          </div>

          <div className="mt-6">
          <SortFilterTable
            filterPlaceholder="Filtrar campaña…"
            columns={[
              { key: 'name', label: 'Campaña' },
              { key: 'seller_name', label: 'SDR/AE' },
              {
                key: 'meetings_scheduled',
                label: 'Reuniones (módulo)',
                sortValue: (r) => r.meetings_scheduled ?? 0,
              },
              { key: 'meetings', label: 'Estado tech. agendado', sortValue: (r) => r.meetings },
            ]}
            rows={campaigns}
          />
          </div>
        </>
      )}
    </>
  )
}
