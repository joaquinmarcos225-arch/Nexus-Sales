import { useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useCompany } from '../../context/CompanyContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { PageHeader } from '../../layout/PageHeader'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { useFlattenedProspects } from '../../hooks/useFlattenedProspects.js'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'

const ACTIVE_STATUSES = new Set(['running', 'ready'])

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

const CAMPAIGN_STATUS = {
  draft: 'Borrador',
  ready: 'Lista',
  running: 'En curso',
  paused: 'Pausada',
  completed: 'Completada',
}

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

function channelLabel(ch) {
  const m = { linkedin: 'LinkedIn', email: 'Email', whatsapp: 'WhatsApp' }
  const key = String(ch || '').toLowerCase()
  return m[key] || ch || '—'
}

export default function DashboardSellerDetailPage() {
  const { userId } = useParams()
  const id = Number(userId)
  const { companyId } = useCompany()
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const {
    rows: prospectRows,
    loading: loadingP,
    error: errP,
  } = useFlattenedProspects(companyId)

  const seller = useMemo(() => {
    if (!Number.isFinite(id) || id < 1 || !data?.sellers?.length) {
      return null
    }
    return data.sellers.find((s) => Number(s.user_id) === id) ?? null
  }, [data, id])

  const sellerCampaigns = useMemo(() => {
    if (!seller || !Array.isArray(data?.campaigns)) {
      return []
    }
    return data.campaigns.filter((c) => Number(c.seller_id) === Number(seller.user_id))
  }, [data, seller])

  const activeCampaigns = useMemo(
    () => sellerCampaigns.filter((c) => ACTIVE_STATUSES.has(c.status)),
    [sellerCampaigns],
  )

  const prospectsForSeller = useMemo(() => {
    if (!seller) {
      return []
    }
    return prospectRows
      .filter((p) => Number(p.seller_id) === Number(seller.user_id))
      .map((p) => ({
        ...p,
        status_label: STATUS_LABEL[p.status] ?? p.status,
        interest_label: INTEREST_LABEL[(p.interest_level || 'low').toLowerCase()] ?? p.interest_level,
      }))
  }, [prospectRows, seller])

  return (
    <>
      <PageHeader
        title={seller?.name ?? 'Miembro del equipo'}
        description="Rendimiento y cobertura de campañas (sin datos financieros)."
      />
      <AlertBanner message={error ?? errP} onDismiss={() => void refresh()} />

      {!Number.isFinite(id) || id < 1 ? (
        <p className="text-sm text-nx-muted">Identificador no válido.</p>
      ) : null}

      {companyId && !loading && data && seller == null ? (
        <p className="text-sm text-nx-muted">No encontramos un SDR/AE con ese id en esta empresa.</p>
      ) : null}

      {seller ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <Link
            to="/dashboard/equipo"
            className="font-medium text-nx-brand hover:underline"
          >
            ← Equipo
          </Link>
          <span className="text-nx-muted">·</span>
          <span className="text-nx-muted">{seller.email}</span>
        </div>
      ) : null}

      {loading && !data ? (
        <p className="text-sm text-nx-muted">Cargando…</p>
      ) : null}

      {seller ? (
        <>
          <section className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">
                Campañas activas
              </p>
              <p className="mt-2 text-2xl font-semibold tabular-nums text-nx-ink">
                {activeCampaigns.length}
              </p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">
                Prospectos en campaña
              </p>
              <p className="mt-2 text-2xl font-semibold tabular-nums text-nx-ink">
                {seller.prospects_in_campaigns ?? prospectsForSeller.length}
              </p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">
                Mensajes · respuestas
              </p>
              <p className="mt-2 text-2xl font-semibold tabular-nums text-nx-ink">
                {seller.messages_sent ?? 0} · {seller.responses ?? 0}
              </p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">
                Interés · reuniones · tareas
              </p>
              <p className="mt-2 text-2xl font-semibold tabular-nums text-nx-ink">
                {seller.interested ?? 0} · {seller.meetings ?? 0} · {seller.pending_tasks ?? 0}
              </p>
            </div>
          </section>

          <div className="mb-6 grid gap-4 sm:grid-cols-2">
            <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">Tasa de respuesta</p>
              <p className="mt-2 text-xl font-semibold text-nx-ink">{pct(seller.response_rate)}</p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">Tasa de interés</p>
              <p className="mt-2 text-xl font-semibold text-nx-ink">{pct(seller.interest_rate)}</p>
            </div>
            <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm sm:col-span-2">
              <p className="text-[11px] font-semibold uppercase text-nx-muted">Actividad reciente</p>
              <p className="mt-2 text-sm text-nx-ink">{fmtDate(seller.last_activity_at)}</p>
            </div>
          </div>

          <section className="mb-8 rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-nx-ink">Campañas asignadas</h2>
            <p className="mt-0.5 text-xs text-nx-muted">
              {sellerCampaigns.length} en total · {activeCampaigns.length} en curso / listas para salir.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="min-w-full divide-y divide-nx-border text-sm">
                <thead className="text-left text-xs font-semibold uppercase text-nx-muted">
                  <tr>
                    <th className="py-2 pr-3">Campaña</th>
                    <th className="py-2 pr-3">Estado</th>
                    <th className="py-2 pr-3 tabular-nums">Contactados</th>
                    <th className="py-2 pr-3 tabular-nums">Respuestas</th>
                    <th className="py-2 pr-3 tabular-nums">Reuniones</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-nx-border text-nx-ink">
                  {sellerCampaigns.map((c) => (
                    <tr key={c.campaign_id}>
                      <td className="py-2 pr-3">
                        <Link
                          className="font-medium text-nx-brand hover:underline"
                          to={`/campanas/${c.campaign_id}`}
                        >
                          {c.name}
                        </Link>
                      </td>
                      <td className="py-2 pr-3">{CAMPAIGN_STATUS[c.status] ?? c.status}</td>
                      <td className="py-2 pr-3 tabular-nums">{c.prospects_contacted}</td>
                      <td className="py-2 pr-3 tabular-nums">{c.prospects_responded}</td>
                      <td className="py-2 pr-3 tabular-nums">{c.meetings}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sellerCampaigns.length === 0 ? (
                <p className="mt-3 text-sm text-nx-muted">Sin campañas con este rol asignado.</p>
              ) : null}
            </div>
          </section>

          <section className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-nx-ink">Prospectos asignados</h2>
            {loadingP ? (
              <p className="mt-2 text-sm text-nx-muted">Cargando prospectos…</p>
            ) : prospectsForSeller.length === 0 ? (
              <p className="mt-2 text-sm text-nx-muted">No hay prospectos en campañas de esta persona.</p>
            ) : (
              <div className="mt-3">
                <SortFilterTable
                  filterPlaceholder="Nombre, empresa, estado…"
                  columns={[
                    {
                      key: 'name',
                      label: 'Nombre',
                      render: (r) => <span className="font-medium text-nx-ink">{r.name}</span>,
                    },
                    { key: 'company_name', label: 'Empresa', render: (r) => r.company_name || '—' },
                    { key: 'role', label: 'Rol', render: (r) => r.role || '—' },
                    { key: 'status_label', label: 'Estado' },
                    {
                      key: 'preferred_channel',
                      label: 'Canal',
                      render: (r) => channelLabel(r.preferred_channel),
                    },
                    { key: 'interest_label', label: 'Interés' },
                    {
                      key: 'compatibility_score',
                      label: 'Compatibilidad',
                      sortValue: (r) => r.compatibility_score,
                    },
                    {
                      key: 'last_inbound_at',
                      label: 'Última actividad',
                      sortValue: (r) => r.last_inbound_at || r.updated_at || '',
                      render: (r) => fmtDate(r.last_inbound_at || r.updated_at),
                    },
                    {
                      key: 'campaign_id',
                      label: '',
                      render: (r) => (
                        <Link
                          className="text-xs font-semibold text-nx-brand hover:underline"
                          to={`/campanas/${r.campaign_id}`}
                        >
                          Campaña
                        </Link>
                      ),
                    },
                  ]}
                  rows={prospectsForSeller}
                />
              </div>
            )}
          </section>
        </>
      ) : null}
    </>
  )
}
