import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { CampaignFormModal } from '../components/campaigns/CampaignFormModal.jsx'
import { CampaignStatusBadge } from '../components/campaigns/CampaignStatusBadge.jsx'
import { CampaignOutreachSection } from '../components/outreach/CampaignOutreachSection.jsx'
import { LeadSourcingPanel } from '../components/sourcing/LeadSourcingPanel.jsx'
import { formatChannelsSummary } from '../utils/campaignChannels.js'
import { fetchCampaign, fetchCampaignProspects, fetchProducts, fetchUsers } from '../utils/api.js'

function fmtLastEdit(iso) {
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

function Row({ label, value }) {
  return (
    <div className="flex flex-wrap justify-between gap-x-3 gap-y-0.5 border-b border-slate-50 py-1.5 text-xs last:border-0">
      <span className="text-slate-500">{label}</span>
      <span className="max-w-[16rem] text-right font-medium text-slate-900 sm:max-w-xs">{value ?? '—'}</span>
    </div>
  )
}

export default function CampanaDetallePage() {
  const { campaignId } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { companyId, company } = useCompany()
  const id = Number(campaignId)

  const preferredProspectId = useMemo(() => {
    const raw = searchParams.get('prospect')
    const n = Number(raw)
    return Number.isFinite(n) && n >= 1 ? n : null
  }, [searchParams])

  const focusOutreach = searchParams.get('focus') === 'outreach'

  const [campaign, setCampaign] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [prospectReloadKey, setProspectReloadKey] = useState(0)
  const [outreachProspects, setOutreachProspects] = useState([])
  const [editOpen, setEditOpen] = useState(false)
  const [catalog, setCatalog] = useState({ products: [], sellers: [] })

  useEffect(() => {
    if (focusOutreach && !loading && campaign) {
      const el = document.getElementById('campaign-outreach-section')
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [focusOutreach, loading, campaign])

  const loadCampaign = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchCampaign(id)
      setCampaign(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setCampaign(null)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!Number.isFinite(id) || id < 1) {
      setError('ID de campaña inválido')
      setLoading(false)
      return
    }

    let cancelled = false
    ;(async () => {
      try {
        const data = await fetchCampaign(id)
        if (!cancelled) {
          setCampaign(data)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setCampaign(null)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [id])

  const companyMismatch =
    campaign && companyId != null && Number(campaign.company_id) !== Number(companyId)

  useEffect(() => {
    if (!Number.isFinite(id) || id < 1 || companyMismatch) {
      setOutreachProspects([])
      return
    }
    let cancelled = false
    void fetchCampaignProspects(id).then((data) => {
      if (!cancelled) {
        setOutreachProspects(Array.isArray(data) ? data : [])
      }
    })
    return () => {
      cancelled = true
    }
  }, [id, companyMismatch, prospectReloadKey])

  useEffect(() => {
    if (!companyId) {
      setCatalog({ products: [], sellers: [] })
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const [p, u] = await Promise.all([fetchProducts(companyId), fetchUsers(companyId)])
        if (!cancelled) {
          setCatalog({
            products: Array.isArray(p) ? p : [],
            sellers: (Array.isArray(u) ? u : []).filter((x) => x.role === 'seller'),
          })
        }
      } catch {
        if (!cancelled) {
          setCatalog({ products: [], sellers: [] })
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [companyId])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            to="/campanas"
            className="text-xs font-medium text-nx-brand hover:text-nx-brand-hover"
          >
            ← Campañas
          </Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">
            {campaign?.name ?? 'Carga de campaña…'}
          </h1>
          {company ? <p className="mt-1 text-sm text-slate-500">{company.name}</p> : null}
          {campaign?.updated_at || campaign?.created_at ? (
            <p className="mt-1 text-xs text-slate-400">
              Última edición:{' '}
              <span className="font-medium text-slate-600">
                {campaign?.updated_at
                  ? fmtLastEdit(campaign.updated_at)
                  : 'Sin guardados desde la creación'}
              </span>
              {campaign?.updated_at ? null : (
                <span className="text-slate-400"> · creada {fmtLastEdit(campaign.created_at)}</span>
              )}
            </p>
          ) : null}
        </div>
        {campaign ? (
          <div className="flex flex-col items-end gap-2">
            <CampaignStatusBadge status={campaign.status} />
            <button
              type="button"
              disabled={companyMismatch || !companyId}
              onClick={() => setEditOpen(true)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-800 shadow-sm hover:bg-slate-50 disabled:opacity-40"
            >
              Editar campaña
            </button>
          </div>
        ) : null}
      </div>

      <AlertBanner message={error} />

      {companyMismatch ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-950">
          Esta campaña pertenece a otra empresa. Cambiá la selección desde el header para alinear navegador y
          filtros locales.
        </div>
      ) : null}

      {loading ? <p className="text-sm text-slate-500">Cargando datos de campaña…</p> : null}

      {Number.isFinite(id) && id >= 1 && !companyMismatch ? (
        <LeadSourcingPanel
          campaignId={id}
          campaign={campaign}
          freeze={companyMismatch}
          onImported={() => {
            setProspectReloadKey((v) => v + 1)
            void loadCampaign()
          }}
        />
      ) : null}

      {!loading && campaign ? (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-lg border border-slate-200/90 bg-white p-3 shadow-sm">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">General</h2>
              <Row label="Nombre" value={campaign.name} />
              <Row label="SDR/AE asignado" value={campaign.seller_name} />
              <Row label="Producto" value={campaign.product_name} />
              <Row label="Prospectos a contactar" value={`${campaign.prospect_count}`} />
              <Row label="Tono" value={campaign.tone} />
              <Row label="Timezone" value={campaign.timezone} />
              <Row label="Link Calendar" value={campaign.calendar_link} />
              <Row label="Horarios disponibles" value={campaign.available_hours} />
              <Row label="Canales (orden de prioridad)" value={formatChannelsSummary(campaign.allowed_channels)} />
              <Row
                label="Autopilot"
                value={
                  campaign.autopilot_status === 'running'
                    ? 'Activo'
                    : campaign.autopilot_status === 'paused'
                      ? 'En pausa'
                      : campaign.autopilot_status === 'completed'
                        ? 'Completado'
                        : 'Apagado (manual)'
                }
              />
              <Row label="Nombre remitente" value={campaign.sender_name} />
              <Row label="Email remitente" value={campaign.sender_email} />
              <Row
                label="Contexto IA (campaña)"
                value={
                  campaign.ai_context
                    ? `${String(campaign.ai_context).slice(0, 160)}${String(campaign.ai_context).length > 160 ? '…' : ''}`
                    : '—'
                }
              />
              <Row
                label="Follow-up (días / máx auto)"
                value={
                  [campaign.followup_delay_days, campaign.max_auto_followups].every((x) => x == null)
                    ? 'Default servidor'
                    : `${campaign.followup_delay_days ?? '—'} días · máx ${campaign.max_auto_followups ?? '—'}`
                }
              />
            </div>

            <div className="rounded-lg border border-slate-200/90 bg-white p-3 shadow-sm">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">ICP objetivo</h2>
              <Row label="Tamaño empresa" value={campaign.target_company_size} />
              <Row label="Industria" value={campaign.target_industry} />
              <Row label="País" value={campaign.target_country} />
              <Row label="Idioma" value={campaign.target_language} />
              <Row label="Rol" value={campaign.target_role} />
            </div>
          </div>

          <div id="campaign-outreach-section">
            <CampaignOutreachSection
              campaignId={id}
              companyId={companyId}
              campaign={campaign}
              prospects={outreachProspects}
              preferredProspectId={preferredProspectId}
              freeze={companyMismatch}
              onChanged={() => {
                setProspectReloadKey((v) => v + 1)
                void loadCampaign()
              }}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => navigate('/campanas')}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
            >
              Volver al listado
            </button>
          </div>
        </>
      ) : null}

      <CampaignFormModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        mode="edit"
        campaignId={id}
        initialCampaign={campaign}
        companyId={companyId}
        products={catalog.products}
        sellers={catalog.sellers}
        onSaved={() => {
          setEditOpen(false)
          setProspectReloadKey((v) => v + 1)
          void loadCampaign()
        }}
      />
    </div>
  )
}
