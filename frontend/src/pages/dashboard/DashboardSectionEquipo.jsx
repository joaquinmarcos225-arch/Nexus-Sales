import { Link } from 'react-router-dom'
import { PageHeader } from '../../layout/PageHeader'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

function pct(x) {
  if (x == null || Number.isNaN(x)) {
    return '0%'
  }
  return `${(Number(x) * 100).toFixed(1)}%`
}

function fmtDate(iso) {
  if (!iso) {
    return '—'
  }
  try {
    return new Date(iso).toLocaleString('es-AR', {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return '—'
  }
}

export default function DashboardSectionEquipo() {
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const sellers = (data?.sellers ?? []).map((s) => ({ ...s, id: s.user_id }))

  const chartData = sellers.map((s) => ({
    name: s.name.length > 14 ? `${s.name.slice(0, 13)}…` : s.name,
    mensajes: s.messages_sent,
    respuestas: s.responses,
    reuniones: s.meetings,
    tasaResp: Math.round((s.response_rate ?? 0) * 1000) / 10,
    tasaInt: Math.round((s.interest_rate ?? 0) * 1000) / 10,
  }))

  return (
    <>
      <PageHeader
        title="Equipo (SDR / AE)"
        description="Rendimiento operativo por persona (sin datos financieros)."
      />
      <AlertBanner message={error} onDismiss={() => void refresh()} />

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando…</p>
      ) : (
        <>
          <div className="mb-4 h-72 w-full rounded-xl border border-nx-border bg-nx-card p-4">
            <p className="mb-2 text-xs font-semibold text-nx-muted">Volumen y tasas por SDR/AE</p>
            <ResponsiveContainer width="100%" height="88%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 28 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} domain={[0, 100]} />
                <Tooltip />
                <Legend />
                <Bar yAxisId="left" dataKey="mensajes" fill="#b91c1c" name="Mensajes" radius={[4, 4, 0, 0]} />
                <Bar yAxisId="left" dataKey="respuestas" fill="#991b1b" name="Respuestas" radius={[4, 4, 0, 0]} />
                <Bar yAxisId="left" dataKey="reuniones" fill="#7f1d1d" name="Reuniones" radius={[4, 4, 0, 0]} />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="tasaResp"
                  stroke="#0f766e"
                  name="% respuesta"
                  strokeWidth={2}
                  dot
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="tasaInt"
                  stroke="#0369a1"
                  name="% interés"
                  strokeWidth={2}
                  dot
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <SortFilterTable
            filterPlaceholder="Buscar SDR/AE…"
            columns={[
              {
                key: 'name',
                label: 'SDR/AE',
                render: (r) => (
                  <Link
                    className="font-medium text-nx-brand hover:underline"
                    to={`/dashboard/equipo/${r.user_id}`}
                  >
                    {r.name}
                  </Link>
                ),
              },
              { key: 'email', label: 'Email' },
              {
                key: 'active_campaigns',
                label: 'Campañas activas',
                sortValue: (r) => r.active_campaigns ?? 0,
                render: (r) => String(r.active_campaigns ?? 0),
              },
              {
                key: 'prospects_in_campaigns',
                label: 'Prospectos asignados',
                sortValue: (r) => r.prospects_in_campaigns,
              },
              {
                key: 'messages_sent',
                label: 'Mensajes enviados',
                sortValue: (r) => r.messages_sent,
              },
              {
                key: 'responses',
                label: 'Respuestas',
                sortValue: (r) => r.responses,
              },
              {
                key: 'interested',
                label: 'Interesados',
                sortValue: (r) => r.interested,
              },
              {
                key: 'meetings',
                label: 'Reuniones',
                sortValue: (r) => r.meetings,
              },
              {
                key: 'response_rate',
                label: 'Tasa respuesta',
                sortValue: (r) => r.response_rate ?? 0,
                render: (r) => pct(r.response_rate ?? 0),
              },
              {
                key: 'interest_rate',
                label: 'Tasa interés',
                sortValue: (r) => r.interest_rate ?? 0,
                render: (r) => pct(r.interest_rate ?? 0),
              },
              {
                key: 'pending_tasks',
                label: 'Tareas pendientes (aprox.)',
                sortValue: (r) => r.pending_tasks,
                render: (r) => String(r.pending_tasks ?? 0),
              },
              {
                key: 'last_activity_at',
                label: 'Última actividad',
                sortValue: (r) => r.last_activity_at ?? '',
                render: (r) => fmtDate(r.last_activity_at),
              },
            ]}
            rows={sellers}
          />
        </>
      )}
    </>
  )
}
