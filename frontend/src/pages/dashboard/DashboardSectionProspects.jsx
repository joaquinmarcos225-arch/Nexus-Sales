import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../../layout/PageHeader'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'
import { useCompany } from '../../context/CompanyContext.jsx'
import { useFlattenedProspects } from '../../hooks/useFlattenedProspects.js'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  prospectNextMilestoneSummary,
  sequenceCalendarDayIndex,
  sequenceGroupLabel,
  sequenceStateLabel,
} from '../../utils/sequenceUi.js'

const STATUS_LABEL = {
  imported: 'Importado',
  compatible: 'Compatible',
  not_compatible: 'No compatible',
  contacted: 'Contactado',
  replied: 'Respondió',
  interested: 'Interesado',
  not_interested: 'No interesado',
  meeting_booked: 'Reunión',
  failed: 'Fallido',
}

const INTEREST_LABEL = {
  low: 'Bajo',
  medium: 'Medio',
  high: 'Alto',
}

const DONUT_COLORS = ['#b91c1c', '#991b1b', '#7f1d1d', '#dc2626', '#f87171', '#fecaca', '#9ca3af', '#4b5563']

function channelLabel(ch) {
  const m = { linkedin: 'LinkedIn', email: 'Email', whatsapp: 'WhatsApp' }
  const key = String(ch || '').toLowerCase()
  return m[key] || ch || '—'
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

function lastInteractionIso(r) {
  const cands = [r.last_inbound_at, r.last_outbound_at, r.last_followup_at, r.updated_at].filter(Boolean)
  if (!cands.length) {
    return null
  }
  return cands.sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0]
}

export default function DashboardSectionProspects() {
  const { companyId } = useCompany()
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const { rows: prospectRows, loading: loadingP, error: errP } =
    useFlattenedProspects(companyId)
  const [openCampaigns, setOpenCampaigns] = useState(() => new Set())

  const breakdown = data?.prospect_status_breakdown ?? {}
  const funnelData = Object.entries(breakdown).map(([k, v]) => ({
    estado: STATUS_LABEL[k] ?? k,
    cantidad: v,
  }))

  const tableRows = prospectRows.map((p) => ({
    ...p,
    status_label: STATUS_LABEL[p.status] ?? p.status,
    interest_label: INTEREST_LABEL[(p.interest_level || 'low').toLowerCase()] ?? p.interest_level,
  }))

  const byCampaign = useMemo(() => {
    const m = new Map()
    for (const p of tableRows) {
      const label = (p.campaign_name || '').trim() || 'Sin campaña'
      if (!m.has(label)) {
        m.set(label, [])
      }
      m.get(label).push(p)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], 'es'))
  }, [tableRows])

  return (
    <>
      <PageHeader
        title="Prospectos"
        description="Lista por campaña con nombre, empresa, rol, estado, canal, interés y compatibilidad. Arribá: contexto rápido en gráficos."
      />
      <AlertBanner message={error ?? errP} onDismiss={() => void refresh()} />

      {!companyId ? (
        <p className="rounded-lg border border-dashed border-nx-border bg-nx-card p-4 text-sm text-nx-muted">
          Seleccioná una empresa en el header para ver prospectos y tablas.
        </p>
      ) : (
        <>
          {loading && !data ? (
            <p className="text-sm text-nx-muted">Cargando métricas…</p>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="h-64 w-full rounded-xl border border-nx-border bg-nx-card p-4">
                <p className="mb-2 text-xs font-semibold text-nx-muted">Estados de prospectos</p>
                <ResponsiveContainer width="100%" height="85%">
                  <PieChart>
                    <Pie
                      data={funnelData.length ? funnelData : [{ estado: 'Sin datos', cantidad: 1 }]}
                      dataKey="cantidad"
                      nameKey="estado"
                      cx="50%"
                      cy="50%"
                      innerRadius={48}
                      outerRadius={72}
                      paddingAngle={2}
                    >
                      {(funnelData.length ? funnelData : [{ estado: 'Sin datos', cantidad: 1 }]).map(
                        (_, i) => (
                          <Cell key={String(i)} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />
                        ),
                      )}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="h-64 w-full rounded-xl border border-nx-border bg-nx-card p-4">
                <p className="mb-2 text-xs font-semibold text-nx-muted">
                  Compatibilidad (histograma por score)
                </p>
                <ResponsiveContainer width="100%" height="85%">
                  <BarChart
                    data={(() => {
                      const bins = [0, 20, 40, 60, 80, 100]
                      const c = [0, 0, 0, 0, 0]
                      for (const p of prospectRows) {
                        const v = Math.max(0, Math.min(100, Number(p.compatibility_score) || 0))
                        for (let i = 0; i < bins.length - 1; i++) {
                          const lo = bins[i]
                          const hi = bins[i + 1]
                          if (i === bins.length - 2) {
                            if (v >= lo && v <= hi) {
                              c[i] += 1
                              break
                            }
                          } else if (v >= lo && v < hi) {
                            c[i] += 1
                            break
                          }
                        }
                      }
                      return c.map((count, i) => ({
                        rango: `${bins[i]}-${bins[i + 1]}`,
                        count,
                      }))
                    })()}
                    margin={{ top: 8, right: 8, left: 0, bottom: 8 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="rango" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip />
                    <Bar dataKey="count" fill="#7f1d1d" name="Prospectos" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          <section className="mt-8">
            <h2 className="text-sm font-semibold text-nx-ink">Prospectos por campaña</h2>
            <p className="mt-0.5 text-xs text-nx-muted">
              Tocá el nombre de la campaña para expandir. Por defecto todo colapsado para una vista limpia.
            </p>

            {loadingP ? (
              <p className="mt-4 text-sm text-nx-muted">Cargando prospectos…</p>
            ) : byCampaign.length === 0 ? (
              <div className="mt-4 rounded-xl border border-nx-border bg-nx-card p-10 text-center shadow-sm">
                <p className="text-sm text-nx-ink">
                  Todavía no hay prospectos. Creá una campaña e iniciá el outreach para que Nexus empiece a
                  generar prospectos.
                </p>
                <Link
                  to="/campanas"
                  className="mt-5 inline-flex rounded-lg bg-nx-brand px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-nx-brand-hover"
                >
                  Ir a Campañas
                </Link>
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                {byCampaign.map(([campaignName, rows]) => {
                  const open = openCampaigns.has(campaignName)
                  return (
                    <div
                      key={campaignName}
                      className="overflow-hidden rounded-xl border border-nx-border bg-nx-card shadow-sm"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setOpenCampaigns((prev) => {
                            const next = new Set(prev)
                            if (next.has(campaignName)) {
                              next.delete(campaignName)
                            } else {
                              next.add(campaignName)
                            }
                            return next
                          })
                        }}
                        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-nx-card-muted/60"
                      >
                        <div>
                          <h3 className="text-sm font-semibold text-nx-ink">Prospectos · {campaignName}</h3>
                          <p className="mt-0.5 text-xs text-nx-muted">{rows.length} en esta campaña</p>
                        </div>
                        <span className="shrink-0 text-nx-muted" aria-hidden>
                          {open ? '▾' : '▸'}
                        </span>
                      </button>
                      {open ? (
                        <div className="border-t border-nx-border px-2 pb-4 pt-1 sm:px-3">
                          <SortFilterTable
                            filterPlaceholder="Buscar en esta campaña…"
                            columns={[
                              {
                                key: 'name',
                                label: 'Nombre',
                                render: (r) => (
                                  <span className="font-medium text-nx-ink">{r.name}</span>
                                ),
                              },
                              { key: 'company_name', label: 'Empresa', render: (r) => r.company_name || '—' },
                              {
                                key: 'sequence_group',
                                label: 'Grupo',
                                render: (r) => sequenceGroupLabel(r.sequence_group),
                              },
                              {
                                key: 'sequence_state',
                                label: 'Estado seq.',
                                render: (r) => sequenceStateLabel(r.sequence_state),
                              },
                              {
                                key: 'seq_day',
                                label: 'Día seq.',
                                sortValue: (r) => sequenceCalendarDayIndex(r.sequence_started_at) || 0,
                                render: (r) => {
                                  const d = sequenceCalendarDayIndex(r.sequence_started_at)
                                  return d > 0 ? `Día ${d}` : '—'
                                },
                              },
                              {
                                key: 'next_channel',
                                label: 'Próximo canal',
                                render: (r) => prospectNextMilestoneSummary(r, null).channelLabel,
                              },
                              { key: 'status_label', label: 'Estado CRM' },
                              {
                                key: 'preferred_channel',
                                label: 'Canal pref.',
                                render: (r) => channelLabel(r.preferred_channel),
                              },
                              { key: 'interest_label', label: 'Interés' },
                              {
                                key: 'compatibility_score',
                                label: 'Compatibilidad',
                                sortValue: (r) => r.compatibility_score,
                              },
                              {
                                key: 'last_touch',
                                label: 'Última interacción',
                                sortValue: (r) => lastInteractionIso(r) || '',
                                render: (r) => fmtDate(lastInteractionIso(r)),
                              },
                              {
                                key: 'campaign_id',
                                label: '',
                                sortValue: () => '',
                                render: (r) => (
                                  <Link
                                    to={`/campanas/${r.campaign_id}`}
                                    className="text-xs font-semibold text-nx-brand hover:underline"
                                  >
                                    Ver campaña
                                  </Link>
                                ),
                              },
                            ]}
                            rows={rows}
                          />
                        </div>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            )}
          </section>
        </>
      )}
    </>
  )
}
