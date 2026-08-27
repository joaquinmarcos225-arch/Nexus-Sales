import { PageHeader } from '../../layout/PageHeader'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'
import { PiePercentLabel } from '../../components/charts/ChartLabels.jsx'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { NX_CHART, NX_CHART_GRID, NX_CHART_LEGEND, NX_CHART_SENTIMENT, NX_CHART_TOOLTIP, averageBy, chartAvgCaption, enrichSlicesWithPct, pieTooltipWithPct } from '../../utils/chartTheme.js'

function pct(x) {
  if (x == null || Number.isNaN(x)) {
    return '0%'
  }
  return `${(Number(x) * 100).toFixed(1)}%`
}

const OBJ_LABELS = {
  send_info: 'Mandame info',
  no_time: 'Ahora no',
  competitor: 'Ya uso otra herramienta',
  not_interested: 'No me interesa',
  wants_meeting: 'Quiere reunión',
}

function objectionLabel(key) {
  return OBJ_LABELS[key] || key
}

export default function DashboardSectionResponses() {
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const points = (data?.responses_by_campaign ?? []).map((p) => ({
    ...p,
    id: p.campaign_id,
  }))

  const weekly = (data?.weekly_inbound_responses ?? []).map((w) => ({
    semana: w.week_label,
    inbound: w.count,
  }))

  const chartData = points.map((p) => ({
    name:
      p.campaign_name.length > 18 ? `${p.campaign_name.slice(0, 17)}…` : p.campaign_name,
    respuestas: p.responses,
  }))

  const sentimentPie = enrichSlicesWithPct(
    [
      { name: 'Positivas', value: data?.responses_positive ?? 0 },
      { name: 'Negativas', value: data?.responses_negative ?? 0 },
      { name: 'Neutras', value: data?.responses_neutral ?? 0 },
    ].filter((x) => x.value > 0),
  )
  const sentimentPieDisplay = sentimentPie.length
    ? sentimentPie
    : [{ name: 'Sin datos', value: 1, pctLabel: '100%' }]

  const avgInbound = averageBy(weekly, 'inbound')

  const objectionEntries = Object.entries(data?.objection_counts ?? {})
    .map(([k, v]) => ({ key: k, label: objectionLabel(k), count: v }))
    .sort((a, b) => b.count - a.count)

  const detailRows = (data?.responses_campaign_detail ?? []).map((row, i) => ({
    ...row,
    id: `${row.campaign_name}-${i}`,
    rate_label: pct(row.response_rate),
  }))

  const hist = data?.interest_histogram ?? []

  return (
    <>
      <PageHeader
        title="Respuestas"
        description="Sentimiento agregado, objeciones, serie de inbound y detalle por campaña."
      />
      <AlertBanner message={error} onDismiss={() => void refresh()} />

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando…</p>
      ) : (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border border-nx-border bg-nx-card p-3">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">Positivas</p>
              <p className="mt-1 text-xl font-semibold text-nx-ink">{data?.responses_positive ?? 0}</p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-3">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">Negativas</p>
              <p className="mt-1 text-xl font-semibold text-nx-ink">{data?.responses_negative ?? 0}</p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-3">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">Neutras</p>
              <p className="mt-1 text-xl font-semibold text-nx-ink">{data?.responses_neutral ?? 0}</p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-3">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">Quieren reunión</p>
              <p className="mt-1 text-xl font-semibold text-nx-ink">{data?.responses_wants_meeting ?? 0}</p>
            </div>
            {data?.avg_reply_hours != null ? (
              <div className="rounded-xl border border-nx-border bg-nx-card p-3 sm:col-span-2">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">
                  Tiempo promedio de respuesta (aprox., h)
                </p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{data.avg_reply_hours}</p>
                <p className="mt-1 text-[11px] text-nx-muted">
                  Basado en último outbound vs inbound por prospecto con réplica.
                </p>
              </div>
            ) : null}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="h-72 w-full rounded-xl border border-nx-border bg-nx-card p-4">
              <p className="mb-0.5 text-xs font-semibold text-nx-muted">Respuestas inbound por semana</p>
              {weekly.length ? (
                <p className="mb-2 text-[10px] text-nx-muted">{chartAvgCaption('Prom. semanal', avgInbound)}</p>
              ) : (
                <p className="mb-2 text-[10px] text-nx-muted">Sin serie semanal aún.</p>
              )}
              <ResponsiveContainer width="100%" height="90%">
                <LineChart data={weekly} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                  <CartesianGrid {...NX_CHART_GRID} />
                  <XAxis dataKey="semana" tick={{ fontSize: 9 }} angle={-15} textAnchor="end" height={40} />
                  <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                  <Tooltip />
                  <Line type="monotone" dataKey="inbound" stroke={NX_CHART.brandHover} strokeWidth={2} dot={false} name="Inbound" />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="h-72 w-full rounded-xl border border-nx-border bg-nx-card p-4">
              <p className="mb-2 text-xs font-semibold text-nx-muted">Distribución de sentimiento (prospectos)</p>
              <ResponsiveContainer width="100%" height="90%">
                <PieChart>
                  <Pie
                    data={sentimentPieDisplay}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={40}
                    outerRadius={70}
                    label={sentimentPie.length ? PiePercentLabel : false}
                    labelLine={false}
                  >
                    {sentimentPieDisplay.map((_, i) => (
                      <Cell
                        key={String(i)}
                        fill={
                          sentimentPie.length
                            ? NX_CHART_SENTIMENT[i % NX_CHART_SENTIMENT.length]
                            : NX_CHART.empty
                        }
                      />
                    ))}
                  </Pie>
                  <Tooltip {...NX_CHART_TOOLTIP} formatter={pieTooltipWithPct} />
                  <Legend {...NX_CHART_LEGEND} formatter={(value, entry) => entry?.payload?.legendLabel || value} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="h-64 w-full rounded-xl border border-nx-border bg-nx-card p-4">
              <p className="mb-2 text-xs font-semibold text-nx-muted">Respuestas por campaña</p>
              <ResponsiveContainer width="100%" height="88%">
                <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 40 }}>
                  <CartesianGrid {...NX_CHART_GRID} />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-20} textAnchor="end" height={52} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="respuestas" fill={NX_CHART.brandDeep} name="Respuestas" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-4">
              <p className="mb-2 text-xs font-semibold text-nx-muted">Interés modelado (histograma)</p>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={hist} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                  <CartesianGrid {...NX_CHART_GRID} />
                  <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="count" fill={NX_CHART.brandDark} name="Prospectos" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {objectionEntries.length ? (
            <div className="mt-4 rounded-xl border border-nx-border bg-nx-card p-4">
              <p className="text-xs font-semibold text-nx-muted">Objeciones frecuentes</p>
              <ul className="mt-2 grid gap-2 sm:grid-cols-2">
                {objectionEntries.slice(0, 12).map((o) => (
                  <li key={o.key} className="flex justify-between rounded-lg bg-nx-bg px-3 py-2 text-sm">
                    <span>{o.label}</span>
                    <span className="font-semibold text-nx-ink">{o.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="mt-4">
            <SortFilterTable
              filterPlaceholder="Filtrar campaña…"
              columns={[
                { key: 'campaign_name', label: 'Campaña' },
                {
                  key: 'responses_total',
                  label: 'Respuestas',
                  sortValue: (r) => r.responses_total,
                },
                {
                  key: 'positive',
                  label: 'Positivas',
                  sortValue: (r) => r.positive,
                },
                {
                  key: 'negative',
                  label: 'Negativas',
                  sortValue: (r) => r.negative,
                },
                {
                  key: 'neutral',
                  label: 'Neutras',
                  sortValue: (r) => r.neutral,
                },
                {
                  key: 'high_interest',
                  label: 'Interés alto',
                  sortValue: (r) => r.high_interest,
                },
                { key: 'top_objection', label: 'Objeción principal' },
                {
                  key: 'rate_label',
                  label: 'Tasa respuesta',
                  sortValue: (r) => r.response_rate,
                },
              ]}
              rows={detailRows}
            />
          </div>
        </>
      )}
    </>
  )
}
