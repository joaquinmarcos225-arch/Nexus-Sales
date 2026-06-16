import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { CampaignFormModal } from '../components/campaigns/CampaignFormModal.jsx'
import { CampaignStatusBadge } from '../components/campaigns/CampaignStatusBadge.jsx'
import { PageHeader } from '../layout/PageHeader'
import { SortFilterTable } from '../components/dashboard/SortFilterTable.jsx'
import {
  fetchCampaigns,
  fetchCompanyAnalytics,
  fetchProducts,
  fetchUsers,
} from '../utils/api.js'
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

const STATUS_LABEL = {
  draft: 'Borrador',
  ready: 'Lista',
  running: 'En curso',
  paused: 'Pausada',
  completed: 'Completada',
}

const PIE_COL = ['#b91c1c', '#991b1b', '#7f1d1d', '#dc2626', '#9ca3af']

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

function shortLabel(name, max = 16) {
  const s = (name || '').trim()
  if (s.length <= max) {
    return s
  }
  return `${s.slice(0, max - 1)}…`
}

function pctRate(contacted, responded) {
  const c = Number(contacted) || 0
  const r = Number(responded) || 0
  if (c <= 0) {
    return 0
  }
  return Math.round((r / c) * 1000) / 10
}

export default function CampanasPage() {
  const { companyId, loading: ctxLoading } = useCompany()
  const [campaigns, setCampaigns] = useState([])
  const [products, setProducts] = useState([])
  const [sellers, setSellers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editCampaign, setEditCampaign] = useState(null)

  const [analyticsData, setAnalyticsData] = useState(null)
  const [analyticsLoading, setAnalyticsLoading] = useState(false)
  const [analyticsError, setAnalyticsError] = useState(null)

  const loadData = useCallback(async () => {
    if (!companyId) {
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [cList, pList, uList] = await Promise.all([
        fetchCampaigns(companyId),
        fetchProducts(companyId),
        fetchUsers(companyId),
      ])
      setCampaigns(Array.isArray(cList) ? cList : [])
      setProducts(Array.isArray(pList) ? pList : [])
      setSellers(Array.isArray(uList) ? uList.filter((u) => u.role === 'seller') : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setCampaigns([])
      setProducts([])
      setSellers([])
    } finally {
      setLoading(false)
    }
  }, [companyId])

  const loadAnalytics = useCallback(async () => {
    if (!companyId) {
      setAnalyticsData(null)
      setAnalyticsError(null)
      return
    }
    setAnalyticsLoading(true)
    setAnalyticsError(null)
    try {
      const res = await fetchCompanyAnalytics(companyId)
      setAnalyticsData(res)
    } catch (e) {
      setAnalyticsError(e instanceof Error ? e.message : String(e))
      setAnalyticsData(null)
    } finally {
      setAnalyticsLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void loadData()
  }, [loadData])

  useEffect(() => {
    void loadAnalytics()
  }, [loadAnalytics])

  const totals = analyticsData?.totals

  const analyticsRows = useMemo(() => {
    const raw = analyticsData?.campaigns ?? []
    return raw.map((c) => ({
      ...c,
      id: c.campaign_id,
      status_label: STATUS_LABEL[c.status] ?? c.status,
      response_rate_pct: pctRate(c.prospects_contacted, c.prospects_responded),
    }))
  }, [analyticsData])

  /** Si analytics aún no trae filas pero ya hay campañas, mostramos ceros para gráficos/tablas. */
  const displayRows = useMemo(() => {
    if (analyticsRows.length > 0) {
      return analyticsRows
    }
    return campaigns.map((c) => ({
      campaign_id: c.id,
      name: c.name,
      status: c.status,
      seller_id: c.seller_id,
      seller_name: c.seller_name ?? '—',
      prospects_active: 0,
      prospects_contacted: 0,
      prospects_responded: 0,
      prospects_interested: 0,
      prospects_not_interested: 0,
      prospects_replied: 0,
      meetings: 0,
      meetings_scheduled: 0,
      messages_sent: 0,
      last_activity_at: null,
      id: c.id,
      status_label: STATUS_LABEL[c.status] ?? c.status,
      response_rate_pct: 0,
    }))
  }, [analyticsRows, campaigns])

  const statusMix = useMemo(() => {
    const m = new Map()
    for (const c of displayRows) {
      const lab = STATUS_LABEL[c.status] ?? c.status
      m.set(lab, (m.get(lab) || 0) + 1)
    }
    return [...m.entries()].map(([name, value]) => ({ name, value }))
  }, [displayRows])

  const chartVolume = useMemo(
    () =>
      displayRows.map((c) => ({
        name: shortLabel(c.name),
        prospectos: c.prospects_active,
        mensajes: c.messages_sent,
        respuestas: c.prospects_responded,
        interesados: c.prospects_interested,
        reuniones: c.meetings,
      })),
    [displayRows],
  )

  const chartRates = useMemo(
    () =>
      displayRows.map((c) => ({
        name: shortLabel(c.name),
        tasaRespuesta: pctRate(c.prospects_contacted, c.prospects_responded),
      })),
    [displayRows],
  )

  async function refreshAll() {
    await Promise.all([loadData(), loadAnalytics()])
  }

  return (
    <>
      <PageHeader
        title="Campañas"
        description="Definí ICP, canales, tono y prospectos meta; luego gestioná outreach y seguimiento desde cada campaña."
        actions={
          <button
            type="button"
            disabled={ctxLoading || !companyId}
            className="rounded-lg bg-nx-brand px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-40"
            onClick={() => setModalOpen(true)}
          >
            Crear campaña
          </button>
        }
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />
      <AlertBanner message={analyticsError} onDismiss={() => setAnalyticsError(null)} />

      <p className="mb-4 text-xs text-[#6b7280]">
        Las integraciones con herramientas externas se conectan en una fase posterior.
      </p>

      {(loading || ctxLoading) && companyId ? (
        <p className="text-sm text-[#6b7280]">Cargando campañas...</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-xl border border-dashed border-[#e5e7eb] bg-white px-4 py-8 text-center text-sm text-[#6b7280] shadow-sm">
          Sin empresa seleccionada (revisá el backend y `/companies`).
        </p>
      ) : null}

      {companyId && !loading ? (
        <>
          {analyticsLoading && !analyticsData ? (
            <p className="mb-4 text-sm text-[#6b7280]">Cargando métricas…</p>
          ) : null}

          {totals ? (
            <section className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-[#6b7280]">
                  Campañas activas
                </p>
                <p className="mt-2 text-2xl font-semibold text-[#111827]">{totals.campaigns_active}</p>
              </div>
              <div className="rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-[#6b7280]">
                  Mensajes enviados
                </p>
                <p className="mt-2 text-2xl font-semibold text-[#111827]">{totals.messages_sent}</p>
              </div>
              <div className="rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-[#6b7280]">
                  Contactados / Respuestas
                </p>
                <p className="mt-2 text-2xl font-semibold text-[#111827]">
                  {totals.prospects_contacted} / {totals.prospects_responded}
                </p>
              </div>
              <div className="rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase text-[#6b7280]">
                  Interesados / Reuniones
                </p>
                <p className="mt-2 text-2xl font-semibold text-[#111827]">
                  {totals.prospects_interested} / {totals.meetings_booked}
                </p>
              </div>
            </section>
          ) : null}

          {campaigns.length > 0 && displayRows.length > 0 ? (
            <div className="mb-8 space-y-6">
              <div className="grid gap-6 lg:grid-cols-3">
                <div className="h-80 rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
                  <p className="mb-2 text-xs font-semibold text-[#6b7280]">Campañas por estado</p>
                  <ResponsiveContainer width="100%" height="88%">
                    <PieChart>
                      <Pie
                        data={statusMix.length ? statusMix : [{ name: 'Sin datos', value: 1 }]}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={36}
                        outerRadius={64}
                      >
                        {(statusMix.length ? statusMix : [{ name: 'Sin datos', value: 1 }]).map(
                          (_, i) => (
                            <Cell key={String(i)} fill={PIE_COL[i % PIE_COL.length]} />
                          ),
                        )}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="lg:col-span-2 h-80 rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
                  <p className="mb-2 text-xs font-semibold text-[#6b7280]">
                    Por campaña: prospectos activos, mensajes, respuestas, interesados y reuniones
                  </p>
                  <ResponsiveContainer width="100%" height="88%">
                    <BarChart data={chartVolume} margin={{ top: 8, right: 8, left: 0, bottom: 48 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-22} textAnchor="end" height={58} />
                      <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="prospectos" fill="#94a3b8" name="Prospectos activos" />
                      <Bar dataKey="mensajes" fill="#fecaca" name="Mensajes" />
                      <Bar dataKey="respuestas" fill="#b91c1c" name="Respuestas" />
                      <Bar dataKey="interesados" fill="#dc2626" name="Interesados" />
                      <Bar dataKey="reuniones" fill="#7f1d1d" name="Reuniones" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="h-72 rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
                <p className="mb-2 text-xs font-semibold text-[#6b7280]">
                  Tasa de respuesta por campaña (% sobre contactados)
                </p>
                <ResponsiveContainer width="100%" height="88%">
                  <BarChart data={chartRates} margin={{ top: 8, right: 8, left: 0, bottom: 48 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-22} textAnchor="end" height={58} />
                    <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} allowDecimals />
                    <Tooltip formatter={(v) => [`${v}%`, 'Tasa respuesta']} />
                    <Legend />
                    <Bar dataKey="tasaRespuesta" fill="#0f766e" name="% respuesta" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : null}
        </>
      ) : null}

      {!loading && campaigns.length === 0 && companyId ? (
        <div className="rounded-xl border border-dashed border-[#e5e7eb] bg-white p-12 text-center text-sm text-[#6b7280] shadow-sm">
          Aún no hay campañas. Creá una para definir el ICP, importar prospectos y dar seguimiento.
        </div>
      ) : null}

      {campaigns.length ? (
        <section className="overflow-hidden rounded-xl border border-[#e5e7eb] bg-white shadow-sm shadow-[#111827]/5">
          <div className="border-b border-[#e5e7eb] px-4 py-3">
            <h2 className="text-sm font-semibold text-[#111827]">Detalle por campaña</h2>
            <p className="mt-0.5 text-xs text-[#6b7280]">
              Métricas operativas (analytics) y acceso al detalle. Si falla el resumen, usá la tabla rápida de
              abajo.
            </p>
          </div>
          <div className="p-4">
            <SortFilterTable
              filterPlaceholder="Filtrar por nombre, SDR/AE, estado…"
              columns={[
                {
                  key: 'name',
                  label: 'Campaña',
                  render: (r) => (
                    <Link
                      className="font-medium text-sky-700 hover:text-sky-900 hover:underline"
                      to={`/campanas/${r.campaign_id}`}
                    >
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
                  key: 'response_rate_pct',
                  label: '% respuesta',
                  sortValue: (r) => r.response_rate_pct,
                  render: (r) => `${r.response_rate_pct}%`,
                },
                {
                  key: 'last_activity_at',
                  label: 'Última actividad',
                  sortValue: (r) => r.last_activity_at ?? '',
                  render: (r) => fmtDate(r.last_activity_at),
                },
                {
                  key: '_actions',
                  label: '',
                  render: (r) => {
                    const c = campaigns.find((x) => x.id === r.campaign_id)
                    if (!c) {
                      return '—'
                    }
                    return (
                      <button
                        type="button"
                        className="text-xs font-semibold text-sky-700 hover:underline"
                        onClick={() => setEditCampaign(c)}
                      >
                        Editar campaña
                      </button>
                    )
                  },
                },
                {
                  key: '_meta',
                  label: 'ICP / meta',
                  sortValue: (r) => {
                    const c = campaigns.find((x) => x.id === r.campaign_id)
                    return c ? `${c.target_country ?? ''} ${c.prospect_count}` : ''
                  },
                  render: (r) => {
                    const c = campaigns.find((x) => x.id === r.campaign_id)
                    if (!c) {
                      return '—'
                    }
                    return (
                      <span className="text-xs text-[#6b7280]">
                        {c.target_country ?? '—'} · meta {c.prospect_count}
                      </span>
                    )
                  },
                },
              ]}
              rows={displayRows}
            />
          </div>
        </section>
      ) : null}

      <CampaignFormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        companyId={companyId}
        products={products}
        sellers={sellers}
        onCreated={() => {
          void refreshAll()
        }}
      />
      <CampaignFormModal
        open={!!editCampaign}
        onClose={() => setEditCampaign(null)}
        mode="edit"
        campaignId={editCampaign?.id ?? null}
        initialCampaign={editCampaign}
        companyId={companyId}
        products={products}
        sellers={sellers}
        onSaved={() => {
          setEditCampaign(null)
          void refreshAll()
        }}
      />
    </>
  )
}
