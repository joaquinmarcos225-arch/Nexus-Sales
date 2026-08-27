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
import { NX_CHART, NX_CHART_GRID, NX_CHART_TOOLTIP, averageBy, chartAvgCaption, formatPctNumber, formatPctRate } from '../../utils/chartTheme.js'

function equipoTooltipFormatter(value, name) {
  if (name === '% respuesta' || name === '% interés') {
    return [formatPctNumber(value), name]
  }
  return [value, name]
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

  const avgTasaResp = averageBy(chartData, 'tasaResp')
  const avgTasaInt = averageBy(chartData, 'tasaInt')

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
            <p className="mb-0.5 text-xs font-semibold text-nx-muted">Volumen y tasas por SDR/AE</p>
            <p className="mb-2 text-[10px] text-nx-muted">
              {chartAvgCaption('Prom. % respuesta', avgTasaResp, { suffix: '%' })} ·{' '}
              {chartAvgCaption('Prom. % interés', avgTasaInt, { suffix: '%' })}
            </p>
            <ResponsiveContainer width="100%" height="88%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 28 }}>
                <CartesianGrid {...NX_CHART_GRID} />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} domain={[0, 100]} />
                <Tooltip {...NX_CHART_TOOLTIP} formatter={equipoTooltipFormatter} />
                <Legend />
                <Bar yAxisId="left" dataKey="mensajes" fill={NX_CHART.brandHover} name="Mensajes" radius={[4, 4, 0, 0]} />
                <Bar yAxisId="left" dataKey="respuestas" fill={NX_CHART.brandDark} name="Respuestas" radius={[4, 4, 0, 0]} />
                <Bar yAxisId="left" dataKey="reuniones" fill={NX_CHART.brandDeep} name="Reuniones" radius={[4, 4, 0, 0]} />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="tasaResp"
                  stroke={NX_CHART.brand}
                  name="% respuesta"
                  strokeWidth={2}
                  dot
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="tasaInt"
                  stroke={NX_CHART.brandLight}
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
                render: (r) => formatPctRate(r.response_rate ?? 0),
              },
              {
                key: 'interest_rate',
                label: 'Tasa interés',
                sortValue: (r) => r.interest_rate ?? 0,
                render: (r) => formatPctRate(r.interest_rate ?? 0),
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
