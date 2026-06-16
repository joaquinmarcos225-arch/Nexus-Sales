import { useEffect, useState } from 'react'
import { PageHeader } from '../../layout/PageHeader'
import { useCompany } from '../../context/CompanyContext.jsx'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'
import { fetchCompanyMeetings } from '../../utils/api.js'
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

function fmt(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return '—'
  }
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

  useEffect(() => {
    if (!companyId) {
      setMeetings([])
      return
    }
    let c = false
    void fetchCompanyMeetings(companyId)
      .then((list) => {
        if (!c) setMeetings(Array.isArray(list) ? list : [])
      })
      .catch(() => {
        if (!c) setMeetings([])
      })
    return () => {
      c = true
    }
  }, [companyId, data])

  return (
    <>
      <PageHeader
        title="Reuniones"
        description="Módulo Meeting + métricas de pipeline comercial (simulado; Calendar externo después)."
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
            <p className="mb-2 text-xs font-semibold text-nx-muted">
              Reuniones completadas por semana (fecha agendada)
            </p>
            <ResponsiveContainer width="100%" height="85%">
              <LineChart data={weekly} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="week_label" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="count"
                  stroke="#0369a1"
                  strokeWidth={2}
                  name="Completadas"
                  dot={{ fill: '#0c4a6e' }}
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
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-20} textAnchor="end" height={48} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="reuniones" fill="#0284c7" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-6">
            <p className="mb-2 text-xs font-semibold text-nx-muted">Registros recientes (módulo Meeting)</p>
            <div className="overflow-x-auto rounded-xl border border-nx-border bg-nx-card">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead className="border-b border-nx-border bg-nx-card-muted/40 text-[11px] uppercase text-nx-muted">
                  <tr>
                    <th className="px-3 py-2 font-semibold">Título</th>
                    <th className="px-3 py-2 font-semibold">Cuándo</th>
                    <th className="px-3 py-2 font-semibold">Estado</th>
                    <th className="px-3 py-2 font-semibold">TZ / min</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-nx-border">
                  {meetings.slice(0, 20).map((m) => (
                    <tr key={m.id} className="hover:bg-nx-card-muted/30">
                      <td className="max-w-[240px] truncate px-3 py-2 text-nx-ink" title={m.title}>
                        {m.title}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-nx-muted">{fmt(m.scheduled_for)}</td>
                      <td className="px-3 py-2 text-xs capitalize text-nx-ink">{m.meeting_status}</td>
                      <td className="px-3 py-2 text-xs text-nx-muted">
                        {m.timezone} · {m.duration_minutes}m
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!meetings.length ? (
                <p className="px-3 py-8 text-center text-sm text-nx-muted">
                  Sin reuniones registradas. Aceptá una sugerencia IA desde la conversación de un prospecto.
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
