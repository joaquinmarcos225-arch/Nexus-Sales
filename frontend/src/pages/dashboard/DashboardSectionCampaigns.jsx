import { useMemo } from 'react'
import { Link } from 'react-router-dom'
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
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { NX_CHART, NX_CHART_BAR, NX_CHART_GRID, NX_CHART_LEGEND, NX_CHART_MARGIN, NX_CHART_SERIES, NX_CHART_TOOLTIP, NX_CHART_VOLUME, NX_CHART_Y_TICK, averageBy, chartAvgCaption, enrichSlicesWithPct, pieTooltipWithPct } from '../../utils/chartTheme.js'
import { formatLocalDateTime } from '../../utils/instantFormat.js'

function fmtDate(iso) {
  return formatLocalDateTime(iso)
}

const STATUS_LABEL = {
  draft: 'Borrador',
  ready: 'Lista',
  running: 'En curso',
  paused: 'Pausada',
  completed: 'Completada',
}

export default function DashboardSectionCampaigns() {
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const rows = (data?.campaigns ?? []).map((c) => ({
    ...c,
    id: c.campaign_id,
    status_label: STATUS_LABEL[c.status] ?? c.status,
  }))
  const t = data?.totals

  const chartData = rows.map((c) => ({
    name: c.name.length > 18 ? `${c.name.slice(0, 17)}…` : c.name,
    respuestas: c.prospects_responded,
    reuniones: c.meetings,
    mensajes: c.messages_sent,
  }))

  const statusMix = useMemo(() => {
    const m = new Map()
    for (const c of rows) {
      const lab = STATUS_LABEL[c.status] ?? c.status
      m.set(lab, (m.get(lab) || 0) + 1)
    }
    return enrichSlicesWithPct([...m.entries()].map(([name, value]) => ({ name, value })))
  }, [rows])

  const avgMensajes = averageBy(chartData, 'mensajes')
  const avgRespuestas = averageBy(chartData, 'respuestas')

  return (
    <>
      <PageHeader
        title="Campañas"
        description="Resumen global, distribución por estado y detalle por campaña."
      />
      <AlertBanner message={error} onDismiss={() => void refresh()} />

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando…</p>
      ) : (
        <>
          {t ? (
            <section className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Campañas activas</p>
                <p className="mt-2 text-2xl font-semibold text-nx-ink">{t.campaigns_active}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Mensajes enviados</p>
                <p className="mt-2 text-2xl font-semibold text-nx-ink">{t.messages_sent}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Contactados / Respuestas</p>
                <p className="mt-2 text-2xl font-semibold text-nx-ink">
                  {t.prospects_contacted} / {t.prospects_responded}
                </p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Interesados / Reuniones</p>
                <p className="mt-2 text-2xl font-semibold text-nx-ink">
                  {t.prospects_interested} / {t.meetings_booked}
                </p>
              </div>
            </section>
          ) : null}

          <div className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2 h-80 rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="mb-0.5 text-xs font-semibold text-nx-muted">
                Por campaña: respuestas, reuniones y mensajes
              </p>
              <p className="mb-2 text-[10px] text-nx-muted">
                {chartAvgCaption('Prom. mensajes', avgMensajes)} · {chartAvgCaption('Prom. respuestas', avgRespuestas)}
              </p>
              <ResponsiveContainer width="100%" height="88%">
                <BarChart data={chartData} margin={NX_CHART_MARGIN.labeledX}>
                  <CartesianGrid {...NX_CHART_GRID} />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-22} textAnchor="end" height={58} />
                  <YAxis tick={NX_CHART_Y_TICK} allowDecimals={false} />
                  <Tooltip {...NX_CHART_TOOLTIP} />
                  <Legend {...NX_CHART_LEGEND} />
                  <Bar dataKey="mensajes" fill={NX_CHART_VOLUME.mensajes} name="Mensajes" {...NX_CHART_BAR} />
                  <Bar dataKey="respuestas" fill={NX_CHART_VOLUME.respuestas} name="Respuestas" {...NX_CHART_BAR} />
                  <Bar dataKey="reuniones" fill={NX_CHART_VOLUME.reuniones} name="Reuniones" {...NX_CHART_BAR} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="h-80 rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="mb-2 text-xs font-semibold text-nx-muted">Campañas por estado</p>
              <ResponsiveContainer width="100%" height="88%">
                <PieChart>
                  <Pie
                    data={statusMix.length ? statusMix : [{ name: 'Sin datos', value: 1, pctLabel: '100%' }]}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={36}
                    outerRadius={64}
                    label={PiePercentLabel}
                    labelLine={false}
                  >
                    {(statusMix.length ? statusMix : [{ name: 'Sin datos', value: 1 }]).map((_, i) => (
                      <Cell key={String(i)} fill={NX_CHART_SERIES[i % NX_CHART_SERIES.length]} />
                    ))}
                  </Pie>
                  <Tooltip {...NX_CHART_TOOLTIP} formatter={pieTooltipWithPct} />
                  <Legend {...NX_CHART_LEGEND} formatter={(value, entry) => entry?.payload?.legendLabel || value} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          <section className="mt-8 rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-nx-ink">Detalle por campaña</h3>
            <p className="mt-1 text-xs text-nx-muted">Métricas operativas y enlace al detalle.</p>
            <div className="mt-4">
              <SortFilterTable
                filterPlaceholder="Filtrar por nombre, SDR/AE, estado…"
                columns={[
                  {
                    key: 'name',
                    label: 'Campaña',
                    render: (r) => (
                      <Link className="font-medium text-nx-brand hover:underline" to={`/campanas/${r.campaign_id}`}>
                        {r.name}
                      </Link>
                    ),
                  },
                  { key: 'status_label', label: 'Estado' },
                  { key: 'seller_name', label: 'SDR/AE' },
                  { key: 'prospects_active', label: 'Activos', sortValue: (r) => r.prospects_active },
                  { key: 'prospects_contacted', label: 'Contactados', sortValue: (r) => r.prospects_contacted },
                  { key: 'prospects_responded', label: 'Respondieron', sortValue: (r) => r.prospects_responded },
                  { key: 'prospects_interested', label: 'Interesados', sortValue: (r) => r.prospects_interested },
                  { key: 'meetings', label: 'Reuniones', sortValue: (r) => r.meetings },
                  { key: 'messages_sent', label: 'Mensajes', sortValue: (r) => r.messages_sent },
                  {
                    key: 'last_activity_at',
                    label: 'Última actividad',
                    sortValue: (r) => r.last_activity_at ?? '',
                    render: (r) => fmtDate(r.last_activity_at),
                  },
                ]}
                rows={rows}
              />
            </div>
          </section>
        </>
      )}
    </>
  )
}
