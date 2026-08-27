import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCompany } from '../context/CompanyContext.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { CampaignFormModal } from '../components/campaigns/CampaignFormModal.jsx'
import { PiePercentLabel, BarTopLabel } from '../components/charts/ChartLabels.jsx'
import { CampaignStatusBadge } from '../components/campaigns/CampaignStatusBadge.jsx'
import { PageHeader } from '../layout/PageHeader'
import { SortFilterTable } from '../components/dashboard/SortFilterTable.jsx'
import { StatCard } from '../components/ui/Card.jsx'
import { PageSection } from '../components/ui/PageSection.jsx'
import { CollapsibleSection } from '../components/ui/CollapsibleSection.jsx'
import {
  fetchCampaigns,
  fetchCompanyAnalytics,
  fetchCreditAllocations,
  fetchProducts,
  fetchWallet,
  deleteCampaign,
} from '../utils/api.js'
import { confirmDeleteCampaign } from '../utils/confirmDeleteCampaign.js'
import { isIndividualContainerCampaign } from '../utils/individualCampaign.js'
import { isCompanyAdmin, isManagerOrGerente, normalizeRole } from '../data/navigation.js'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  LabelList,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  NX_CHART_BAR,
  NX_CHART_GRID,
  NX_CHART_LEGEND,
  NX_CHART_MARGIN,
  NX_CHART_SERIES,
  NX_CHART_TOOLTIP,
  NX_CHART_VOLUME,
  NX_CHART_Y_TICK,
  formatPctTooltip,
  enrichSlicesWithPct,
  pieTooltipWithPct,
  chartAvgCaption,
  averageBy,
} from '../utils/chartTheme.js'

const STATUS_LABEL = {
  draft: 'Borrador',
  ready: 'Lista',
  running: 'En curso',
  paused: 'Pausada',
  completed: 'Completada',
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
  const { user } = useAuth()
  const [campaigns, setCampaigns] = useState([])
  const [products, setProducts] = useState([])
  const [allocations, setAllocations] = useState([])
  const [walletUnassigned, setWalletUnassigned] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [deletingId, setDeletingId] = useState(null)

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
      const [cList, pList, aList] = await Promise.all([
        fetchCampaigns(companyId),
        fetchProducts(companyId),
        fetchCreditAllocations(companyId),
      ])
      setCampaigns(Array.isArray(cList) ? cList : [])
      setProducts(Array.isArray(pList) ? pList : [])
      setAllocations(Array.isArray(aList) ? aList : [])
      if (isCompanyAdmin({ role: normalizeRole(user?.role) })) {
        try {
          const wallet = await fetchWallet(companyId)
          const total = Number(wallet?.total_balance) || 0
          const assigned = Number(wallet?.assigned_to_sellers) || 0
          setWalletUnassigned(Math.max(0, total - assigned))
        } catch {
          setWalletUnassigned(null)
        }
      } else {
        setWalletUnassigned(null)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setCampaigns([])
      setProducts([])
      setAllocations([])
      setWalletUnassigned(null)
    } finally {
      setLoading(false)
    }
  }, [companyId, user?.role])

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
  const teamCampaignView = isManagerOrGerente(user)
  const isDirectorView = isCompanyAdmin({ role: normalizeRole(user?.role) })
  const viewerId = user?.user_id ?? user?.id

  const analyticsRows = useMemo(() => {
    const raw = analyticsData?.campaigns ?? []
    const mapped = raw.map((c) => ({
      ...c,
      id: c.campaign_id,
      status_label: STATUS_LABEL[c.status] ?? c.status,
      response_rate_pct: pctRate(c.prospects_contacted, c.prospects_responded),
    }))
    if (teamCampaignView) return mapped
    return mapped.filter(
      (c) =>
        Number(c.seller_id) === Number(viewerId) || isIndividualContainerCampaign(c),
    )
  }, [analyticsData, teamCampaignView, viewerId])

  const visibleCampaigns = useMemo(() => {
    if (teamCampaignView) return campaigns
    return campaigns.filter(
      (c) =>
        Number(c.seller_id) === Number(viewerId) || isIndividualContainerCampaign(c),
    )
  }, [campaigns, teamCampaignView, viewerId])

  const sellerCreditAvailable = useMemo(() => {
    if (isCompanyAdmin({ role: normalizeRole(user?.role) }) && walletUnassigned != null) {
      return walletUnassigned
    }
    const uid = user?.user_id ?? user?.id
    if (!uid) return null
    const row = allocations.find((a) => a.seller_id === uid)
    if (!row) return null
    return Math.max(0, Number(row.allocated_balance) - Number(row.used_balance))
  }, [allocations, user?.id, user?.user_id, user?.role, walletUnassigned])

  /** Si analytics aún no trae filas pero ya hay campañas, mostramos ceros para gráficos/tablas. */
  const displayRows = useMemo(() => {
    if (analyticsRows.length > 0) {
      return analyticsRows
    }
    return visibleCampaigns.map((c) => ({
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
  }, [analyticsRows, visibleCampaigns])

  /** Directores: propias vs equipo. Resto: una sola lista. */
  const myCampaignRows = useMemo(() => {
    if (!isDirectorView) return displayRows
    return displayRows.filter((r) => Number(r.seller_id) === Number(viewerId))
  }, [displayRows, isDirectorView, viewerId])

  const teamCampaignRows = useMemo(() => {
    if (!isDirectorView) return []
    return displayRows.filter((r) => Number(r.seller_id) !== Number(viewerId))
  }, [displayRows, isDirectorView, viewerId])

  const statusMix = useMemo(() => {
    const m = new Map()
    for (const c of displayRows) {
      const lab = STATUS_LABEL[c.status] ?? c.status
      m.set(lab, (m.get(lab) || 0) + 1)
    }
    return enrichSlicesWithPct([...m.entries()].map(([name, value]) => ({ name, value })))
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

  const avgTasaRespuesta = averageBy(chartRates, 'tasaRespuesta')

  async function refreshAll() {
    await Promise.all([loadData(), loadAnalytics()])
  }

  async function handleDeleteCampaign(row) {
    const c = campaigns.find((x) => x.id === row.campaign_id) || row
    if (isIndividualContainerCampaign(c)) {
      setError('La campaña «Secuencias individuales» no se puede eliminar.')
      return
    }
    const ok = confirmDeleteCampaign(c, {
      prospectsCount: Number(row.prospects_active) || 0,
    })
    if (!ok) return
    setDeletingId(row.campaign_id)
    setError(null)
    try {
      await deleteCampaign(row.campaign_id)
      await refreshAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeletingId(null)
    }
  }

  const campaignTableColumns = [
    {
      key: 'name',
      label: 'Campaña',
      className: 'max-w-[9rem] truncate',
      thClassName: 'w-[18%]',
      render: (r) => (
        <Link
          className="block truncate font-medium text-nx-brand hover:text-nx-brand-hover hover:underline"
          to={`/campanas/${r.campaign_id}`}
          title={r.name}
        >
          {r.name}
        </Link>
      ),
    },
    {
      key: 'status_label',
      label: 'Estado',
      thClassName: 'w-[7%]',
      className: 'whitespace-nowrap',
    },
    {
      key: 'seller_name',
      label: 'Asignado',
      thClassName: 'w-[9%]',
      className: 'max-w-[5.5rem] truncate',
      render: (r) => (
        <span className="block truncate" title={r.seller_name}>
          {r.seller_name}
        </span>
      ),
    },
    {
      key: 'quota',
      label: 'Cupo',
      thClassName: 'w-[6%]',
      className: 'tabular-nums whitespace-nowrap',
      sortValue: (r) => {
        const c = campaigns.find((x) => x.id === r.campaign_id)
        const target = Number(c?.prospect_count) || 0
        const cur = Number(r.prospects_active) || 0
        return target > 0 ? cur / target : 0
      },
      render: (r) => {
        const c = campaigns.find((x) => x.id === r.campaign_id)
        const target = Number(c?.prospect_count) || 0
        const cur = Number(r.prospects_active) || 0
        if (target <= 0) return '—'
        const met = cur >= target
        return (
          <span className={met ? 'font-semibold text-red-700' : 'text-nx-ink'}>
            {cur}/{target}
          </span>
        )
      },
    },
    {
      key: 'prospects_contacted',
      label: 'Cont.',
      thClassName: 'w-[5%]',
      className: 'tabular-nums text-center',
      sortValue: (r) => r.prospects_contacted,
    },
    {
      key: 'prospects_responded',
      label: 'Resp.',
      thClassName: 'w-[5%]',
      className: 'tabular-nums text-center',
      sortValue: (r) => r.prospects_responded,
    },
    {
      key: 'prospects_interested',
      label: 'Int.',
      thClassName: 'w-[5%]',
      className: 'tabular-nums text-center',
      sortValue: (r) => r.prospects_interested,
    },
    {
      key: 'meetings',
      label: 'Reu.',
      thClassName: 'w-[5%]',
      className: 'tabular-nums text-center',
      sortValue: (r) => r.meetings,
    },
    {
      key: 'messages_sent',
      label: 'Msg',
      thClassName: 'w-[5%]',
      className: 'tabular-nums text-center',
      sortValue: (r) => r.messages_sent,
    },
    {
      key: 'response_rate_pct',
      label: '%',
      thClassName: 'w-[5%]',
      className: 'tabular-nums text-center whitespace-nowrap',
      sortValue: (r) => r.response_rate_pct,
      render: (r) => `${r.response_rate_pct}%`,
    },
    {
      key: 'last_activity_at',
      label: 'Actividad',
      thClassName: 'w-[10%]',
      className: 'whitespace-nowrap text-[11px] text-nx-muted',
      sortValue: (r) => r.last_activity_at ?? '',
      render: (r) => fmtDate(r.last_activity_at),
    },
    {
      key: '_meta',
      label: 'ICP',
      thClassName: 'w-[10%]',
      className: 'max-w-[6rem] truncate text-[11px] text-nx-muted',
      sortValue: (r) => {
        const c = campaigns.find((x) => x.id === r.campaign_id)
        return c ? `${c.target_country ?? ''} ${c.prospect_count}` : ''
      },
      render: (r) => {
        const c = campaigns.find((x) => x.id === r.campaign_id)
        if (!c) return '—'
        const label = c.target_country ?? '—'
        return (
          <span className="block truncate" title={label}>
            {label}
          </span>
        )
      },
    },
    {
      key: '_actions',
      label: '',
      thClassName: 'w-[4.5rem]',
      className: 'text-right',
      sortValue: () => '',
      render: (r) => {
        const c = campaigns.find((x) => x.id === r.campaign_id) || r
        if (isIndividualContainerCampaign(c)) {
          return (
            <span
              className="text-[10px] text-nx-subtle"
              title="Contenedor fijo: no se puede eliminar"
            >
              —
            </span>
          )
        }
        return (
          <button
            type="button"
            disabled={deletingId === r.campaign_id}
            className="rounded border border-red-200 bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold text-red-800 hover:bg-red-100 disabled:opacity-60"
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              void handleDeleteCampaign(r)
            }}
          >
            {deletingId === r.campaign_id ? '…' : 'Borrar'}
          </button>
        )
      },
    },
  ]

  function renderCampaignTableSection({
    id,
    title,
    description,
    rows,
    emptyHint,
    defaultOpen = true,
  }) {
    return (
      <div className="mb-6">
        <CollapsibleSection
          id={id}
          title={title}
          subtitle={description}
          badge={String(rows.length)}
          defaultOpen={defaultOpen}
        >
          {rows.length === 0 ? (
            <p className="py-4 text-center text-sm text-nx-muted">{emptyHint}</p>
          ) : (
            <SortFilterTable
              compact
              stickyLast
              filterPlaceholder="Filtrar por nombre, asignado, estado…"
              columns={campaignTableColumns}
              rows={rows}
            />
          )}
        </CollapsibleSection>
      </div>
    )
  }

  return (
    <>
      <PageHeader
        kicker="Operaciones"
        title="Campañas"
        description="Creá campañas y entrá al detalle para outreach y secuencias. El CRM del cliente es la fuente de verdad de contactos."
        actions={
          <button
            type="button"
            disabled={ctxLoading || !companyId}
            className="nx-btn nx-btn-primary"
            onClick={() => setModalOpen(true)}
          >
            Crear campaña
          </button>
        }
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />
      <AlertBanner message={analyticsError} onDismiss={() => setAnalyticsError(null)} />

      {(loading || ctxLoading) && companyId ? (
        <p className="text-sm text-nx-muted">Cargando campañas...</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="nx-card-muted rounded-xl border border-dashed px-4 py-8 text-center text-sm text-nx-muted">
          Sin empresa seleccionada (revisá el backend y `/companies`).
        </p>
      ) : null}

      {!loading && visibleCampaigns.length === 0 && companyId ? (
        <div className="nx-card-muted mb-6 rounded-xl border border-dashed p-12 text-center text-sm text-nx-muted">
          Aún no hay campañas. Creá una para definir el ICP, o insertá un prospecto puntual eligiendo
          producto abajo.
        </div>
      ) : null}

      {companyId && !loading && visibleCampaigns.length > 0 ? (
        <>
          {analyticsLoading && !analyticsData ? (
            <p className="mb-4 text-sm text-nx-muted">Cargando métricas…</p>
          ) : null}

          {totals ? (
            <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Campañas activas" value={String(totals.campaigns_active)} />
              <StatCard label="Mensajes enviados" value={String(totals.messages_sent)} />
              <StatCard
                label="Contactados / respuestas"
                value={`${totals.prospects_contacted} / ${totals.prospects_responded}`}
              />
              <StatCard
                label="Interesados / reuniones"
                value={`${totals.prospects_interested} / ${totals.meetings_booked}`}
              />
            </div>
          ) : null}

          {isDirectorView ? (
            <>
              {renderCampaignTableSection({
                id: 'campanas-propias',
                title: 'Tus campañas',
                description:
                  'Campañas asignadas a vos. Abrí el detalle para outreach, cola y secuencias.',
                rows: myCampaignRows,
                emptyHint:
                  'Todavía no tenés campañas propias. Creá una o mirá las del equipo abajo.',
                defaultOpen: true,
              })}
              {renderCampaignTableSection({
                id: 'campanas-equipo',
                title: 'Campañas del equipo',
                description: 'Campañas de managers y SDRs. Podés abrirlas para seguimiento.',
                rows: teamCampaignRows,
                emptyHint: 'Nadie del equipo tiene campañas todavía.',
                defaultOpen: myCampaignRows.length === 0,
              })}
            </>
          ) : (
            renderCampaignTableSection({
              id: 'campanas-propias',
              title: 'Tus campañas',
              description: 'Abrí el detalle para outreach, cola y secuencias.',
              rows: myCampaignRows,
              emptyHint: 'Aún no hay campañas en esta vista.',
              defaultOpen: true,
            })
          )}
        </>
      ) : null}

      {companyId && !loading && visibleCampaigns.length > 0 && displayRows.length > 0 ? (
            <PageSection
              title="Gráficos y analítica"
              description="Volumen por campaña, estados y tasas de respuesta."
              defaultOpen={false}
              className="mb-6"
            >
              <div className="space-y-6">
                <div className="grid gap-6 lg:grid-cols-3">
                  <div className="h-80 rounded-xl border border-nx-border bg-nx-card p-4">
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
                          label={statusMix.length ? PiePercentLabel : false}
                          labelLine={false}
                        >
                          {(statusMix.length ? statusMix : [{ name: 'Sin datos', value: 1 }]).map(
                            (_, i) => (
                              <Cell key={String(i)} fill={NX_CHART_SERIES[i % NX_CHART_SERIES.length]} />
                            ),
                          )}
                        </Pie>
                        <Tooltip {...NX_CHART_TOOLTIP} formatter={pieTooltipWithPct} />
                        <Legend {...NX_CHART_LEGEND} formatter={(value, entry) => entry?.payload?.legendLabel || value} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="h-80 rounded-xl border border-nx-border bg-nx-card p-4 lg:col-span-2">
                    <p className="mb-2 text-xs font-semibold text-nx-muted">
                      Por campaña: activos, mensajes, respuestas, interesados y reuniones
                    </p>
                    <ResponsiveContainer width="100%" height="88%">
                      <BarChart data={chartVolume} margin={NX_CHART_MARGIN.labeledX}>
                        <CartesianGrid {...NX_CHART_GRID} />
                        <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-22} textAnchor="end" height={58} />
                        <YAxis tick={NX_CHART_Y_TICK} allowDecimals={false} />
                        <Tooltip {...NX_CHART_TOOLTIP} />
                        <Legend {...NX_CHART_LEGEND} />
                        <Bar
                          dataKey="prospectos"
                          fill={NX_CHART_VOLUME.prospectos}
                          name="Prospectos activos"
                          {...NX_CHART_BAR}
                        />
                        <Bar dataKey="mensajes" fill={NX_CHART_VOLUME.mensajes} name="Mensajes" {...NX_CHART_BAR} />
                        <Bar
                          dataKey="respuestas"
                          fill={NX_CHART_VOLUME.respuestas}
                          name="Respuestas"
                          {...NX_CHART_BAR}
                        />
                        <Bar
                          dataKey="interesados"
                          fill={NX_CHART_VOLUME.interesados}
                          name="Interesados"
                          {...NX_CHART_BAR}
                        />
                        <Bar
                          dataKey="reuniones"
                          fill={NX_CHART_VOLUME.reuniones}
                          name="Reuniones"
                          {...NX_CHART_BAR}
                        />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="h-72 rounded-xl border border-nx-border bg-nx-card p-4">
                  <p className="mb-0.5 text-xs font-semibold text-nx-muted">
                    Tasa de respuesta por campaña (% sobre contactados)
                  </p>
                  <p className="mb-2 text-[10px] text-nx-muted">
                    {chartAvgCaption('Prom. empresa', avgTasaRespuesta, { suffix: '%' })}
                  </p>
                  <ResponsiveContainer width="100%" height="88%">
                    <BarChart data={chartRates} margin={NX_CHART_MARGIN.labeledX}>
                      <CartesianGrid {...NX_CHART_GRID} />
                      <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-22} textAnchor="end" height={58} />
                      <YAxis tick={NX_CHART_Y_TICK} domain={[0, 100]} allowDecimals />
                      <Tooltip {...NX_CHART_TOOLTIP} formatter={formatPctTooltip} />
                      <Legend {...NX_CHART_LEGEND} />
                      <Bar
                        dataKey="tasaRespuesta"
                        fill={NX_CHART_VOLUME.tasaRespuesta}
                        name="% respuesta"
                        {...NX_CHART_BAR}
                      >
                        <LabelList
                          dataKey="tasaRespuesta"
                          position="top"
                          content={(props) => (
                            <BarTopLabel {...props} formatter={(n) => `${n}%`} />
                          )}
                        />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </PageSection>
          ) : null}

      <CampaignFormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        companyId={companyId}
        products={products}
        sellerCreditAvailable={sellerCreditAvailable}
        onCreated={() => {
          void refreshAll()
        }}
      />
    </>
  )
}
