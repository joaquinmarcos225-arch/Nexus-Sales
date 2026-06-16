import { Link } from 'react-router-dom'
import { PageHeader } from '../../layout/PageHeader'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'

const CH_COLORS = ['#b91c1c', '#991b1b', '#7f1d1d', '#dc2626', '#f97316']

function pct(x) {
  if (x == null || Number.isNaN(x)) {
    return '0%'
  }
  return `${(Number(x) * 100).toFixed(1)}%`
}

export default function DashboardSectionOutreach() {
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const rows = (data?.campaigns ?? []).map((c) => ({
    ...c,
    id: c.campaign_id,
    response_rate_msg: c.messages_sent > 0 ? c.prospects_responded / c.messages_sent : 0,
  }))
  const t = data?.totals
  const intel = data?.intelligence

  const chartData = rows.map((c) => ({
    name: c.name.length > 16 ? `${c.name.slice(0, 15)}…` : c.name,
    mensajes: c.messages_sent,
  }))

  const channelData = (data?.outreach_messages_by_channel ?? []).map((row) => ({
    name: String(row.channel || '—'),
    value: Number(row.count) || 0,
  }))

  const scatterData = (data?.scatter_response_vs_messages ?? []).map((row, i) => ({
    x: Number(row.messages_sent) || 0,
    y: Number(row.responses) || 0,
    z: 80,
    name: String(row.campaign || `C${i}`),
  }))

  return (
    <>
      <PageHeader
        title="Outreach"
        description="Volumen outbound, canales y relación mensajes vs respuestas por campaña."
      />
      <AlertBanner message={error} onDismiss={() => void refresh()} />

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando…</p>
      ) : (
        <>
          {t ? (
            <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Mensajes enviados</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{t.messages_sent}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Contactados</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{t.prospects_contacted}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Respondieron</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{t.prospects_responded}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Tasa respuesta</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{pct(t.response_rate)}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Interesados</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{t.prospects_interested}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Sin respuesta (contactados)</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{data?.prospects_no_reply ?? 0}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Follow-ups enviados</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">{data?.followups_sent_total ?? 0}</p>
              </div>
              <div className="rounded-xl border border-nx-border bg-nx-card p-3">
                <p className="text-[11px] font-semibold uppercase text-nx-muted">Follow-ups programados</p>
                <p className="mt-1 text-xl font-semibold text-nx-ink">
                  {intel?.pending_scheduled_followups ?? data?.pending_followups ?? 0}
                </p>
              </div>
            </div>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="h-72 w-full rounded-xl border border-nx-border bg-nx-card p-4">
              <p className="mb-2 text-xs font-semibold text-nx-muted">Mensajes por campaña</p>
              <ResponsiveContainer width="100%" height="90%">
                <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 48 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-20} textAnchor="end" height={56} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="mensajes" fill="#b91c1c" name="Mensajes" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="h-72 w-full rounded-xl border border-nx-border bg-nx-card p-4">
              <p className="mb-2 text-xs font-semibold text-nx-muted">Mensajes por canal</p>
              <ResponsiveContainer width="100%" height="90%">
                <PieChart>
                  <Pie
                    data={channelData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={44}
                    outerRadius={72}
                    paddingAngle={2}
                  >
                    {channelData.map((_, i) => (
                      <Cell key={String(i)} fill={CH_COLORS[i % CH_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="mt-4 h-80 w-full rounded-xl border border-nx-border bg-nx-card p-4">
            <p className="mb-2 text-xs font-semibold text-nx-muted">
              Respuestas vs mensajes enviados (cada punto es una campaña)
            </p>
            <ResponsiveContainer width="100%" height="88%">
              <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis type="number" dataKey="x" name="Mensajes" tick={{ fontSize: 11 }} />
                <YAxis type="number" dataKey="y" name="Respuestas" tick={{ fontSize: 11 }} allowDecimals={false} />
                <ZAxis type="number" dataKey="z" range={[60, 60]} />
                <Tooltip cursor={{ strokeDasharray: '3 3' }} formatter={(v, name) => [v, name]} />
                <Scatter name="Campañas" data={scatterData} fill="#991b1b" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4">
            <SortFilterTable
              filterPlaceholder="Filtrar…"
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
                { key: 'seller_name', label: 'SDR/AE' },
                {
                  key: 'messages_sent',
                  label: 'Mensajes enviados',
                  sortValue: (r) => r.messages_sent,
                },
                {
                  key: 'prospects_contacted',
                  label: 'Contactados',
                  sortValue: (r) => r.prospects_contacted,
                },
                {
                  key: 'prospects_responded',
                  label: 'Respondieron',
                  sortValue: (r) => r.prospects_responded,
                },
                {
                  key: 'prospects_interested',
                  label: 'Interesados',
                  sortValue: (r) => r.prospects_interested,
                },
                {
                  key: 'prospects_not_interested',
                  label: 'No interesados',
                  sortValue: (r) => r.prospects_not_interested ?? 0,
                },
                {
                  key: 'response_rate_msg',
                  label: 'Tasa respuesta (msg)',
                  sortValue: (r) => r.response_rate_msg,
                  render: (r) => pct(r.response_rate_msg),
                },
              ]}
              rows={rows}
            />
          </div>
        </>
      )}
    </>
  )
}
