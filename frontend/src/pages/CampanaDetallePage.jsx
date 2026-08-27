import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { ProspectQuotaBar } from '../components/campaigns/ProspectQuotaBar.jsx'
import { ManualProspectInsertCard } from '../components/campaigns/ManualProspectInsertCard.jsx'
import { ChannelEnrichCountdown } from '../components/campaigns/ChannelEnrichCountdown.jsx'
import { CampaignStatusBadge } from '../components/campaigns/CampaignStatusBadge.jsx'
import { CampaignOutreachSection } from '../components/outreach/CampaignOutreachSection.jsx'
import { CollapsibleSection } from '../components/ui/CollapsibleSection.jsx'
import { ProspectActivityBadge } from '../components/campaigns/ProspectActivityBadge.jsx'
import { formatChannelsSummary } from '../utils/campaignChannels.js'
import { formatLocalDateTime } from '../utils/instantFormat.js'
import {
  deleteCampaign,
  deleteProspect,
  fetchCampaign,
  fetchCampaignOutreach,
  fetchCampaignProspects,
  fetchProducts,
  pauseProspectSequence,
  resumeProspectSequence,
} from '../utils/api.js'
import { confirmDeleteCampaign } from '../utils/confirmDeleteCampaign.js'
import { isIndividualContainerCampaign } from '../utils/individualCampaign.js'
import { clearProspectExtensionWatch } from '../utils/clearProspectExtensionWatch.js'
import { notifyLinkedInQueueChanged } from '../hooks/useLinkedInPending.js'
import { notifyWhatsAppQueueChanged } from '../hooks/useWhatsAppPending.js'

function displayProspectField(value) {
  const s = String(value || '').trim()
  if (!s || s === '—' || s === '-' || s === '?' || s === '??' || /^n\/?a$/i.test(s)) return ''
  return s
}

function displayProspectCompany(name) {
  const s = displayProspectField(name)
  if (!s || /^empresa$/i.test(s)) return ''
  return s
}

function displayProspectRole(role) {
  return displayProspectField(role)
}

function prospectLinkedInHref(prospect) {
  const raw = String(prospect?.linkedin_url || '').trim()
  if (!raw || raw === '?' || raw === '—' || raw === '-') return null
  try {
    const u = new URL(raw.startsWith('http') ? raw : `https://${raw}`)
    if (!/linkedin\.com$/i.test(u.hostname) && !/\.linkedin\.com$/i.test(u.hostname)) {
      return null
    }
    return u.toString()
  } catch {
    return null
  }
}

function ProspectNameLink({ prospect }) {
  const name = displayProspectField(prospect?.name) || 'Sin nombre'
  const href = prospectLinkedInHref(prospect)
  if (!href) {
    return <p className="text-sm font-semibold text-nx-ink">{name}</p>
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-sm font-semibold text-nx-ink underline-offset-2 hover:text-nx-brand hover:underline"
      title="Abrir perfil de LinkedIn"
    >
      {name}
    </a>
  )
}

function prospectWhatsAppDisplay(prospect) {
  const wa = displayProspectField(prospect?.whatsapp)
  if (wa) return wa
  return displayProspectField(prospect?.phone)
}

function ProspectFoundMetaLine({ prospect }) {
  const line = [displayProspectRole(prospect?.role), displayProspectCompany(prospect?.company_name)]
    .filter(Boolean)
    .join(' · ')
  if (!line) return null
  return <p className="mt-0.5 text-[10px] leading-snug text-nx-ink/70">{line}</p>
}

function ProspectFoundContactLines({ prospect }) {
  const email = displayProspectField(prospect?.email)
  const wa = prospectWhatsAppDisplay(prospect)
  const liHref = prospectLinkedInHref(prospect)
  const liLabel = displayProspectField(prospect?.linkedin_url) || liHref || ''

  if (!email && !wa && !liHref) return null

  return (
    <div className="mt-1 space-y-0.5 text-[10px] leading-snug text-nx-ink/70">
      {email ? (
        <p className="truncate max-w-[22rem]">
          <span className="font-medium text-nx-ink/50">Mail:</span> {email}
        </p>
      ) : null}
      {wa ? (
        <p className="truncate max-w-[22rem]">
          <span className="font-medium text-nx-ink/50">WhatsApp:</span> {wa}
        </p>
      ) : null}
      {liHref ? (
        <p className="truncate max-w-[22rem]">
          <span className="font-medium text-nx-ink/50">LinkedIn:</span>{' '}
          <a
            href={liHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-nx-ink underline-offset-2 hover:text-nx-brand hover:underline"
            title="Abrir perfil de LinkedIn"
          >
            {liLabel}
          </a>
        </p>
      ) : null}
    </div>
  )
}

function fmtLastEdit(iso) {
  return formatLocalDateTime(iso)
}

function Row({ label, value }) {
  return (
    <div className="flex flex-wrap justify-between gap-x-3 gap-y-0.5 border-b border-nx-border py-1.5 text-xs last:border-0">
      <span className="text-nx-muted">{label}</span>
      <span className="max-w-[16rem] text-right font-medium text-nx-ink sm:max-w-xs">{value ?? '—'}</span>
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

  const focusParam = searchParams.get('focus')
  const focusNotificaciones =
    focusParam === 'notificaciones' ||
    focusParam === 'linkedin' ||
    focusParam === 'whatsapp' ||
    focusParam === 'mail'
      ? focusParam === 'notificaciones'
        ? 'linkedin'
        : focusParam
      : false
  const focusOutreach = searchParams.get('focus') === 'outreach' || Boolean(focusNotificaciones)

  const [campaign, setCampaign] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [prospectReloadKey, setProspectReloadKey] = useState(0)
  const [outreachProspects, setOutreachProspects] = useState([])
  const [prospectsLoading, setProspectsLoading] = useState(true)
  const [prospectsError, setProspectsError] = useState(null)
  const [seqBusyId, setSeqBusyId] = useState(null)
  const [foundProspectsQuery, setFoundProspectsQuery] = useState('')
  const [campaignStats, setCampaignStats] = useState(null)
  const [products, setProducts] = useState([])

  useEffect(() => {
    if (!companyId) {
      setProducts([])
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const rows = await fetchProducts(companyId)
        if (!cancelled) setProducts(Array.isArray(rows) ? rows : [])
      } catch {
        if (!cancelled) setProducts([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [companyId])

  useEffect(() => {
    if (!loading && campaign) {
      if (focusNotificaciones) {
        const targetId =
          focusNotificaciones === 'whatsapp'
            ? 'campaign-whatsapp'
            : focusNotificaciones === 'mail'
              ? 'campaign-mail'
              : 'campaign-linkedin'
        window.setTimeout(() => {
          document.getElementById(targetId)?.scrollIntoView({
            behavior: 'smooth',
            block: 'start',
          })
        }, 150)
        return
      }
      if (focusOutreach) {
        document.getElementById('campaign-outreach-section')?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        })
      }
    }
  }, [focusNotificaciones, focusOutreach, loading, campaign])

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
      setProspectsLoading(false)
      return
    }
    let cancelled = false
    setProspectsLoading(true)
    setProspectsError(null)
    void fetchCampaignProspects(id)
      .then((data) => {
        if (!cancelled) {
          setOutreachProspects(Array.isArray(data) ? data : [])
          setProspectsError(null)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setProspectsError(e instanceof Error ? e.message : 'No se pudieron cargar los prospectos')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setProspectsLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [id, companyMismatch, prospectReloadKey])

  useEffect(() => {
    if (!Number.isFinite(id) || id < 1 || companyMismatch) {
      setCampaignStats(null)
      return
    }
    let cancelled = false
    void fetchCampaignOutreach(id)
      .then((data) => {
        if (!cancelled) {
          setCampaignStats(data?.stats && typeof data.stats === 'object' ? data.stats : null)
        }
      })
      .catch(() => {
        if (!cancelled) setCampaignStats(null)
      })
    return () => {
      cancelled = true
    }
  }, [id, companyMismatch, prospectReloadKey])

  const campaignRunning = String(campaign?.status || '').toLowerCase() === 'running'

  useEffect(() => {
    if (!campaignRunning || companyMismatch || !Number.isFinite(id) || id < 1) return undefined
    const waitingForImport = outreachProspects.length === 0 || Boolean(prospectsError)
    if (!waitingForImport) return undefined
    const started = Date.now()
    const timer = window.setInterval(() => {
      if (Date.now() - started > 4 * 60_000) {
        window.clearInterval(timer)
        return
      }
      setProspectReloadKey((v) => v + 1)
    }, 6000)
    return () => window.clearInterval(timer)
  }, [campaignRunning, companyMismatch, id, outreachProspects.length, prospectsError])

  const searchingEnrich = useMemo(() => {
    return (outreachProspects || []).filter(
      (p) => String(p?.channel_enrich_status || '').toLowerCase() === 'searching',
    )
  }, [outreachProspects])

  const enrichCountdown = useMemo(() => {
    if (searchingEnrich.length === 0) return null
    const first = searchingEnrich[0]
    const label =
      first?.channel_enrich_message ||
      (searchingEnrich.length === 1
        ? `Buscando datos faltantes de ${first?.name || 'un prospecto'}…`
        : `Buscando datos faltantes de ${searchingEnrich.length} prospectos…`)
    let maxSeconds = 120
    if (first?.channel_enrich_deadline_at) {
      const rem = Math.ceil(
        (new Date(first.channel_enrich_deadline_at).getTime() - Date.now()) / 1000,
      )
      if (Number.isFinite(rem) && rem > 0) maxSeconds = Math.max(rem, 30)
    }
    return {
      label,
      deadlineAt: first?.channel_enrich_deadline_at || null,
      maxSeconds,
    }
  }, [searchingEnrich])

  useEffect(() => {
    if (searchingEnrich.length === 0) return undefined
    const timer = window.setInterval(() => {
      setProspectReloadKey((v) => v + 1)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [searchingEnrich])

  function handleOutreachChanged(patch) {
    if (patch && typeof patch === 'object') {
      setCampaign((prev) => (prev ? { ...prev, ...patch } : prev))
    }
    setProspectReloadKey((v) => v + 1)
    void loadCampaign()
  }

  const prospectsInCampaign = outreachProspects.length
  const prospectsWithEmail = outreachProspects.filter((p) => (p.email || '').trim()).length

  const isIndividualContainer = isIndividualContainerCampaign(campaign)

  /** Solo mostramos hasta la meta pedida (ej. 1 de 1), aunque atrás se hayan encontrado más. */
  const displayProspects = useMemo(() => {
    const meta = Math.max(0, Number(campaign?.prospect_count) || 0)
    if (meta > 0) {
      return outreachProspects.slice(0, meta)
    }
    return outreachProspects
  }, [outreachProspects, campaign?.prospect_count])

  const displayProspectsCount = displayProspects.length

  const filteredFoundProspects = useMemo(() => {
    const needle = foundProspectsQuery.trim().toLowerCase()
    if (!needle) return displayProspects
    return displayProspects.filter((p) => {
      const hay = [
        p?.name,
        p?.company_name,
        p?.role,
        p?.email,
        p?.whatsapp,
        p?.phone,
        p?.linkedin_url,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return hay.includes(needle)
    })
  }, [displayProspects, foundProspectsQuery])

  async function handleDelete() {
    if (!campaign || deleting) return
    const ok = confirmDeleteCampaign(campaign, {
      prospectsCount: prospectsLoading
        ? Number(campaign.prospects_imported) || prospectsInCampaign
        : prospectsInCampaign,
    })
    if (!ok) return
    setDeleting(true)
    setError(null)
    try {
      await deleteCampaign(campaign.id)
      navigate('/campanas', { replace: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setDeleting(false)
    }
  }

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
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-nx-ink">
            {isIndividualContainer
              ? 'Secuencias individuales'
              : (campaign?.name ?? 'Carga de campaña…')}
          </h1>
          {company ? <p className="mt-1 text-sm text-nx-muted">{company.name}</p> : null}
          {campaign?.updated_at || campaign?.created_at ? (
            <p className="mt-1 text-xs text-nx-subtle">
              {campaign?.created_at ? (
                <>
                  Creada:{' '}
                  <span className="font-medium text-nx-muted">{fmtLastEdit(campaign.created_at)}</span>
                </>
              ) : null}
              {campaign?.created_at && campaign?.updated_at ? (
                <span className="text-nx-subtle"> · </span>
              ) : null}
              {campaign?.updated_at ? (
                <>
                  Última edición:{' '}
                  <span className="font-medium text-nx-muted">{fmtLastEdit(campaign.updated_at)}</span>
                </>
              ) : null}
            </p>
          ) : null}
        </div>
        {campaign ? (
          <div className="flex flex-col items-end gap-2">
            <CampaignStatusBadge status={campaign.status} />
            {isIndividualContainer ? (
              <p className="max-w-[14rem] text-right text-[11px] text-nx-subtle">
                Contenedor fijo: no se puede eliminar.
              </p>
            ) : (
              <button
                type="button"
                disabled={deleting}
                className="rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-800 hover:bg-red-100 disabled:opacity-60"
                onClick={() => void handleDelete()}
              >
                {deleting ? 'Eliminando…' : 'Eliminar campaña'}
              </button>
            )}
          </div>
        ) : null}
      </div>

      <AlertBanner message={error} />
      <AlertBanner message={prospectsError} onDismiss={() => setProspectsError(null)} />

      {!loading &&
      campaign &&
      !companyMismatch &&
      !isIndividualContainer &&
      Number(campaign.prospect_count) > 0 ? (
        <div className="rounded-xl border border-nx-border bg-nx-card px-4 py-3 shadow-sm">
          <ProspectQuotaBar
            data-tour="campaign-quota"
            current={prospectsLoading ? campaign.prospects_imported ?? prospectsInCampaign : prospectsInCampaign}
            target={campaign.prospect_count}
            hint="Meta de búsqueda ICP (cuántos contactos querés encontrar). No es lo mismo que ‘ya contactados’."
          />
        </div>
      ) : null}

      {companyMismatch ? (
        <div className="rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 text-xs text-zinc-950">
          Esta campaña pertenece a otra empresa. Cambiá la selección desde el header para alinear navegador y
          filtros locales.
        </div>
      ) : null}

      {loading ? <p className="text-sm text-nx-muted">Cargando datos de campaña…</p> : null}

      {!loading && campaign && !companyMismatch && isIndividualContainer ? (
        <ManualProspectInsertCard
          products={products}
          companyId={companyId}
          onDone={() => {
            setProspectReloadKey((v) => v + 1)
            void loadCampaign()
            notifyLinkedInQueueChanged({ inserted: true })
            notifyWhatsAppQueueChanged({ inserted: true })
          }}
        />
      ) : null}

      {enrichCountdown ? (
        <div className="mb-3">
          <ChannelEnrichCountdown
            active
            label={enrichCountdown.label}
            detail="Nexus consulta Prospeo. Puede tardar hasta 2 minutos; después arranca el plan."
            deadlineAt={enrichCountdown.deadlineAt}
            maxSeconds={enrichCountdown.maxSeconds}
          />
        </div>
      ) : null}

      {!loading && campaign && !companyMismatch ? (
        <div id="campaign-outreach-section">
          <CampaignOutreachSection
            campaignId={id}
            companyId={companyId}
            campaign={campaign}
            prospects={outreachProspects}
            preferredProspectId={preferredProspectId}
            focusNotificaciones={focusNotificaciones}
            freeze={companyMismatch}
            onChanged={handleOutreachChanged}
          />
        </div>
      ) : null}

      {!loading && campaign && !companyMismatch && !isIndividualContainer ? (
        <CollapsibleSection
          id="campaign-found-prospects"
          title="Prospectos encontrados"
          subtitle="Meta ICP: Nexus busca e importa hasta ese cupo. Podés eliminar contactos que no encajen."
          badge={
            prospectsLoading && displayProspectsCount === 0
              ? '…'
              : `${displayProspectsCount}${
                  Number(campaign.prospect_count) > 0 ? ` / ${campaign.prospect_count}` : ''
                }`
          }
          defaultOpen={displayProspectsCount > 0 || campaignRunning}
        >
          {prospectsLoading && displayProspectsCount === 0 ? (
            <p className="text-sm text-nx-ink">Cargando prospectos…</p>
          ) : displayProspectsCount === 0 ? (
            <p className="text-sm text-nx-ink">
              {campaignRunning
                ? 'Nexus está buscando e importando contactos. Los mensajes pueden aparecer antes; esta lista se completa en cuanto cada prospecto queda guardado.'
                : (
                  <>
                    Todavía no hay prospectos. Al{' '}
                    <span className="font-semibold">iniciar la secuencia</span>, Nexus busca e importa
                    según el ICP (hasta la meta).
                  </>
                )}
            </p>
          ) : (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="search"
                  value={foundProspectsQuery}
                  onChange={(e) => setFoundProspectsQuery(e.target.value)}
                  placeholder="Buscar por nombre, empresa, rol o contacto…"
                  className="w-full rounded-lg border border-nx-border bg-white px-2.5 py-1.5 text-[12px] text-nx-ink shadow-sm placeholder:text-nx-muted/70 focus:border-nx-brand/40 focus:outline-none focus:ring-2 focus:ring-nx-brand/15 sm:max-w-md"
                  aria-label="Buscar prospectos encontrados"
                />
                {foundProspectsQuery.trim() ? (
                  <p className="text-[11px] text-nx-muted">
                    {filteredFoundProspects.length} de {displayProspectsCount}
                  </p>
                ) : null}
              </div>
              {filteredFoundProspects.length === 0 ? (
                <p className="text-sm text-nx-ink">Ningún prospecto coincide con la búsqueda.</p>
              ) : (
                /* Misma idea que la cola LinkedIn: grilla en viewport + scroll interno si hay muchos. */
                <div className="max-h-[min(28rem,50vh)] overflow-y-auto overscroll-contain pr-0.5 [scrollbar-gutter:stable]">
                  <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {filteredFoundProspects.map((p) => {
                      const busy = seqBusyId === p.id
                      return (
                        <li
                          key={p.id}
                          className="flex flex-col justify-between gap-2 rounded-xl border border-nx-border/80 bg-white p-3 shadow-sm shadow-nx-ink/5"
                        >
                          <div className="min-w-0">
                            <ProspectNameLink prospect={p} />
                            <ProspectFoundMetaLine prospect={p} />
                            <ProspectActivityBadge prospect={p} className="mt-1" />
                            <ProspectFoundContactLines prospect={p} />
                          </div>
                          <button
                            type="button"
                            disabled={busy}
                            className="self-start rounded-md border border-red-200 bg-red-50 px-2.5 py-1 text-[11px] font-semibold text-red-800 hover:bg-red-100 disabled:opacity-40"
                            onClick={async () => {
                              if (
                                !window.confirm(
                                  `¿Eliminar a ${p.name || 'este prospecto'} de la campaña?`,
                                )
                              ) {
                                return
                              }
                              setSeqBusyId(p.id)
                              setError(null)
                              try {
                                await deleteProspect(p.id)
                                clearProspectExtensionWatch(p.id)
                                notifyLinkedInQueueChanged()
                                notifyWhatsAppQueueChanged()
                                setProspectReloadKey((v) => v + 1)
                                void loadCampaign()
                              } catch (e) {
                                setError(e instanceof Error ? e.message : String(e))
                              } finally {
                                setSeqBusyId(null)
                              }
                            }}
                          >
                            {busy ? '…' : 'Eliminar'}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              )}
            </div>
          )}
        </CollapsibleSection>
      ) : null}

      {!loading && campaign && !companyMismatch && isIndividualContainer ? (
        <CollapsibleSection
          title="Prospectos insertados"
          subtitle="Pausá, reanudá o eliminá cada prospecto sin afectar a los demás"
          badge={prospectsLoading ? '…' : outreachProspects.length}
          defaultOpen={false}
        >
          {prospectsLoading ? (
            <p className="text-sm text-nx-ink">Cargando…</p>
          ) : outreachProspects.length === 0 ? (
            <p className="text-sm text-nx-ink">Todavía no hay prospectos insertados.</p>
          ) : (
            <ul className="divide-y divide-nx-border">
              {outreachProspects.map((p) => {
                const paused = Boolean(p.sequence_paused)
                const busy = seqBusyId === p.id
                return (
                  <li
                    key={p.id}
                    className="flex flex-wrap items-center justify-between gap-2 py-2.5"
                  >
                    <div className="min-w-0">
                      <ProspectNameLink prospect={p} />
                      <p className="mt-0.5 text-xs text-nx-ink/80">
                        {[p.role, p.company_name].filter(Boolean).join(' · ') || '—'}
                        {p.email ? ` · ${p.email}` : ''}
                      </p>
                      <ProspectActivityBadge prospect={p} className="mt-1" />
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {paused ? (
                        <button
                          type="button"
                          disabled={busy}
                          className="rounded-md border border-nx-brand/40 bg-white px-2.5 py-1 text-[11px] font-semibold text-nx-brand hover:bg-nx-brand/5 disabled:opacity-40"
                          onClick={async () => {
                            setSeqBusyId(p.id)
                            setError(null)
                            try {
                              await resumeProspectSequence(p.id)
                              setProspectReloadKey((v) => v + 1)
                              void loadCampaign()
                            } catch (e) {
                              setError(e instanceof Error ? e.message : String(e))
                            } finally {
                              setSeqBusyId(null)
                            }
                          }}
                        >
                          {busy ? '…' : 'Reanudar'}
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled={busy || !p.sequence_started_at}
                          className="rounded-md border border-nx-border bg-white px-2.5 py-1 text-[11px] font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
                          onClick={async () => {
                            setSeqBusyId(p.id)
                            setError(null)
                            try {
                              await pauseProspectSequence(p.id)
                              clearProspectExtensionWatch(p.id)
                              setProspectReloadKey((v) => v + 1)
                              void loadCampaign()
                            } catch (e) {
                              setError(e instanceof Error ? e.message : String(e))
                            } finally {
                              setSeqBusyId(null)
                            }
                          }}
                        >
                          {busy ? '…' : 'Pausar'}
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={busy}
                        className="rounded-md border border-red-200 bg-red-50 px-2.5 py-1 text-[11px] font-semibold text-red-800 hover:bg-red-100 disabled:opacity-40"
                        onClick={async () => {
                          if (
                            !window.confirm(
                              `¿Eliminar a ${p.name || 'este prospecto'} de la campaña?`,
                            )
                          ) {
                            return
                          }
                          setSeqBusyId(p.id)
                          setError(null)
                          try {
                            await deleteProspect(p.id)
                            clearProspectExtensionWatch(p.id)
                            setProspectReloadKey((v) => v + 1)
                            void loadCampaign()
                          } catch (e) {
                            setError(e instanceof Error ? e.message : String(e))
                          } finally {
                            setSeqBusyId(null)
                          }
                        }}
                      >
                        {busy ? '…' : 'Eliminar'}
                      </button>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </CollapsibleSection>
      ) : null}

      {!loading && campaign ? (
        <CollapsibleSection
          title="Datos de la campaña"
          subtitle="Resultados de esta campaña (individuales) + configuración e ICP"
          defaultOpen={false}
        >
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-lg border border-nx-border/90 bg-nx-card-muted/40 p-3">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-nx-muted">
                Resultados de esta campaña
              </h2>
              <Row
                label="Le escribiste a"
                value={
                  campaignStats
                    ? `${Number(campaignStats.contacted) || 0} prospectos`
                    : prospectsLoading
                      ? 'Cargando…'
                      : '—'
                }
              />
              <Row
                label="Te contestaron"
                value={
                  campaignStats
                    ? `${Number(campaignStats.responded) || 0} prospectos`
                    : prospectsLoading
                      ? 'Cargando…'
                      : '—'
                }
              />
              <Row
                label="Escrito → respuesta"
                value={
                  campaignStats
                    ? `${Number(campaignStats.contacted) || 0} → ${Number(campaignStats.responded) || 0}`
                    : '—'
                }
              />
              <Row
                label="Mensajes enviados"
                value={
                  campaignStats != null
                    ? String(Number(campaignStats.messages_outbound) || 0)
                    : '—'
                }
              />
              <Row
                label="Prospectos en campaña"
                value={
                  prospectsLoading
                    ? 'Cargando…'
                    : `${prospectsInCampaign}${prospectsWithEmail > 0 ? ` · ${prospectsWithEmail} con email` : ''}`
                }
              />
              <Row label="Meta ICP (objetivo)" value={`${campaign.prospect_count} contactos`} />
            </div>

            <div className="rounded-lg border border-nx-border/90 bg-nx-card-muted/40 p-3">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-nx-muted">General</h2>
              <Row label="Nombre" value={campaign.name} />
              <Row label="Asignado" value={campaign.seller_name} />
              <Row label="Producto/servicio" value={campaign.product_name} />
              <Row label="Tono" value={campaign.tone} />
              <Row label="Timezone" value={campaign.timezone} />
              <Row
                label="Link Calendar"
                value={campaign.calendar_link?.trim() ? campaign.calendar_link : 'Desde Google Calendar conectado'}
              />
              <Row label="Horarios disponibles" value={campaign.available_hours} />
              <Row label="Canales (orden de prioridad)" value={formatChannelsSummary(campaign.allowed_channels)} />
              <Row
                label="Secuencia Nexus"
                value={
                  campaign.status === 'running' && campaign.automation_paused !== true
                    ? 'En marcha'
                    : campaign.status === 'paused' || campaign.automation_paused
                      ? 'En pausa'
                      : 'Detenida'
                }
              />
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
                label="Follow-up post-secuencia"
                value={
                  campaign.post_sequence_followup_enabled === false
                    ? 'Desactivado'
                    : campaign.followup_delay_days == null
                      ? 'Sí — 30 días (default)'
                      : `Sí — ${campaign.followup_delay_days} días`
                }
              />
            </div>

            <div className="rounded-lg border border-nx-border/90 bg-nx-card-muted/40 p-3">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-nx-muted">ICP objetivo</h2>
              <Row
                label="Modo"
                value={campaign.outreach_mode === 'b2c' ? 'B2C (personas)' : 'B2B (empresas)'}
              />
              {campaign.outreach_mode === 'b2c' ? (
                <>
                  <Row label="Quién buscamos" value={campaign.target_role} />
                  <Row label="Señales / keywords" value={campaign.target_interests} />
                  <Row label="Región" value={campaign.target_country} />
                  <Row label="Idioma (mensajes)" value={campaign.target_language} />
                  <Row label="Situación / momento" value={campaign.target_area} />
                </>
              ) : (
                <>
                  <Row label="Tamaño empresa" value={campaign.target_company_size} />
                  <Row label="Industria" value={campaign.target_industry} />
                  <Row label="Región" value={campaign.target_country} />
                  <Row label="Idioma" value={campaign.target_language} />
                  <Row label="Rol" value={campaign.target_role} />
                </>
              )}
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => navigate('/campanas')}
              className="rounded-lg border border-nx-border px-4 py-2 text-sm text-nx-ink hover:bg-nx-card-muted"
            >
              Volver al listado
            </button>
          </div>
        </CollapsibleSection>
      ) : null}
    </div>
  )
}
