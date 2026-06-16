import { useCallback, useEffect, useMemo, useState } from 'react'
import { LeadSourcingErrorBoundary } from './LeadSourcingErrorBoundary.jsx'
import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import {
  fetchLeadSourcingPipeline,
  fetchLeadSourcingStatus,
  importCampaignLeads,
  resolveApiUrl,
  runLeadSourcingPipeline,
} from '../../utils/api.js'
import { hasRealLinkedInUrl } from '../../utils/linkedinAssist.js'
import { MvpOutreachWorkspace } from './MvpOutreachWorkspace.jsx'
import { MvpCompanyDomainsTable } from './MvpCompanyDomainsTable.jsx'
import {
  MvpContactsTable,
  ProspeoContactDebugPanel,
  ProspeoSearchDebugPanel,
} from './MvpContactsTable.jsx'
import { ProspectingLeadsTable } from './ProspectingLeadsTable.jsx'

const PIPELINE_STEPS_MVP = [
  { key: 'searching_companies', label: 'Web Search' },
  { key: 'companies_found', label: 'Empresas ICP' },
  { key: 'leads_detected', label: 'Cuentas / leads' },
  { key: 'enriching_contacts', label: 'Prospeo' },
  { key: 'ready_to_import', label: 'Nexus Outreach' },
]

const FLOW = ['ICP', 'Web Search', 'Prospeo', 'Nexus Outreach']

const PROVIDER_LABELS = {
  web_search: 'Web Search',
  phantombuster: 'Phantom (exp.)',
  prospeo: 'Prospeo',
}

function isPhantomLogMessage(log) {
  const msg = `${log?.message || ''} ${log?.step || ''}`.toLowerCase()
  return (
    msg.includes('phantom') ||
    msg.includes('phantombuster') ||
    msg.includes('linkedin_agent_id')
  )
}

function isPhantomPanelError(text) {
  const t = (text || '').toLowerCase()
  return t.includes('phantom') || t.includes('phantombuster')
}

const STEP_TIMEOUT_MS = {
  companies: 45000,
  prepare_phantom: 45000,
  extract_companies: 45000,
  people: 160000,
  enrich: 50000,
}

const STEP_LABELS = {
  companies: 'Web Search',
  prepare_phantom: 'Preparar PhantomBuster',
  extract_companies: 'Preparar PhantomBuster',
  people: 'PhantomBuster',
  enrich: 'Prospeo',
}

const STATUS_FETCH_TIMEOUT_MS = 10000
const PIPELINE_FETCH_TIMEOUT_MS = 15000

function formatDuration(ms) {
  if (ms == null || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function eventBadgeClass(event) {
  if (event === 'completed') return 'bg-emerald-100 text-emerald-900'
  if (event === 'started') return 'bg-violet-100 text-violet-900'
  if (event === 'timeout' || event === 'error') return 'bg-rose-100 text-rose-900'
  if (event === 'skipped') return 'bg-amber-100 text-amber-900'
  return 'bg-zinc-100 text-zinc-700'
}

function ContactBadges({ lead }) {
  return (
    <div className="flex flex-wrap gap-1">
      {lead.email ? (
        <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800 ring-1 ring-emerald-100">
          Email
        </span>
      ) : null}
      {lead.linkedin_url && hasRealLinkedInUrl(lead.linkedin_url) ? (
        <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium text-sky-900 ring-1 ring-sky-100">
          LinkedIn
        </span>
      ) : null}
      {lead.phone ? (
        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-700">
          Tel
        </span>
      ) : null}
      {lead.whatsapp ? (
        <span className="rounded bg-green-50 px-1.5 py-0.5 text-[10px] font-medium text-green-900 ring-1 ring-green-100">
          WhatsApp
        </span>
      ) : null}
    </div>
  )
}

/**
 * Lead Sourcing MVP — ICP → Web Search → Prospeo → Nexus Outreach.
 * PhantomBuster queda en sección experimental (opcional).
 */
export function LeadSourcingPanel(props) {
  return (
    <LeadSourcingErrorBoundary>
      <LeadSourcingPanelInner {...props} />
    </LeadSourcingErrorBoundary>
  )
}

function LeadSourcingPanelInner({ campaignId, campaign, freeze = false, onImported }) {
  const [status, setStatus] = useState(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [statusError, setStatusError] = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [pipelineLoading, setPipelineLoading] = useState(false)
  const [pipelineError, setPipelineError] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)
  const [lastFailedStep, setLastFailedStep] = useState('')
  const [importMsg, setImportMsg] = useState(null)
  const [elapsedSec, setElapsedSec] = useState(0)
  const [selected, setSelected] = useState(() => new Set())
  const [showDiscarded, setShowDiscarded] = useState(false)
  const [showPhantomExperimental, setShowPhantomExperimental] = useState(false)
  const [outreachSelectedId, setOutreachSelectedId] = useState('')

  const pipelineReady = status?.configured === true
  const phantomExperimentalAvailable = status?.phantom_experimental === true
  const currentStage = pipeline?.stage || 'idle'
  const people = useMemo(
    () =>
      Array.isArray(pipeline?.people)
        ? pipeline.people.filter((p) => p && typeof p === 'object')
        : [],
    [pipeline],
  )
  const companies = useMemo(
    () =>
      Array.isArray(pipeline?.companies)
        ? pipeline.companies.filter((c) => c && typeof c === 'object')
        : [],
    [pipeline],
  )
  const FORBIDDEN_EMAIL_DOMAINS = new Set([
    'crunchbase.com',
    'wellfound.com',
    'linkedin.com',
    'angellist.com',
    'angel.co',
  ])
  const isForbiddenEmail = useCallback((email) => {
    if (!email || !String(email).includes('@')) return false
    const dom = String(email).split('@')[1]?.toLowerCase().trim()
    return dom ? FORBIDDEN_EMAIL_DOMAINS.has(dom) : false
  }, [])
  const isRealPerson = useCallback(
    (p) => {
      const name = (p.name || '').trim().toLowerCase()
      const company = (p.company_name || '').trim().toLowerCase()
      if (!name || name === company || name === 'contacto') return false
      if ((p.external_id || '').startsWith('icp-account-')) return false
      if (p.contact_kind === 'company_placeholder') return false
      if (isForbiddenEmail(p.email)) return false
      const em = (p.email || '').trim()
      const corp = (p.company_domain || '').trim().toLowerCase()
      if (em && corp && em.includes('@')) {
        const dom = em.split('@')[1]?.toLowerCase()
        if (dom !== corp && !dom.endsWith(`.${corp}`) && corp !== dom) return false
      }
      return Boolean((p.role || '').trim() || (p.linkedin_url || '').trim() || em.includes('@'))
    },
    [isForbiddenEmail],
  )

  const prospectingById = useMemo(() => {
    const map = new Map()
    const rows = pipeline?.prospecting_leads
    if (!Array.isArray(rows)) return map
    for (const row of rows) {
      if (row?.external_id) map.set(row.external_id, row)
    }
    return map
  }, [pipeline?.prospecting_leads])

  const isOutreachReady = useCallback(
    (p) => {
      const row = prospectingById.get(p.external_id)
      if (row) return Boolean(row.outreach_ready)
      return isRealPerson(p) && hasRealLinkedInUrl(p.linkedin_url) && (p.email || '').includes('@')
    },
    [prospectingById, isRealPerson],
  )

  const importable = useMemo(
    () =>
      people.filter(
        (p) => isOutreachReady(p) && !p.already_in_campaign,
      ),
    [people, isOutreachReady],
  )

  const realPeople = useMemo(() => people.filter(isRealPerson), [people, isRealPerson])

  const companyCandidates = useMemo(
    () =>
      companies
        .filter((c) => (c.result_kind || 'company') === 'company')
        .sort((a, b) => (b.icp_relevance_score ?? 0) - (a.icp_relevance_score ?? 0)),
    [companies],
  )
  const companiesWithDomain = useMemo(
    () => companyCandidates.filter((c) => (c.company_domain || '').trim()),
    [companyCandidates],
  )
  const prospeoConfigured = useMemo(() => {
    const providers = status?.providers
    if (!Array.isArray(providers)) return false
    return providers.some((p) => p.name === 'prospeo' && p.configured)
  }, [status])
  const hasEnrichTargets = useMemo(() => {
    if ((pipeline?.companies_count ?? 0) > 0) return true
    if (companyCandidates.length > 0) return true
    if (companiesWithDomain.length > 0) return true
    return false
  }, [pipeline?.companies_count, companyCandidates.length, companiesWithDomain.length])
  const prospeoHealth = useMemo(() => {
    const live = status?.prospeo_health
    const stored = pipeline?.prospeo_health
    const h = live && typeof live === 'object' ? live : stored
    if (!h || typeof h !== 'object') return null
    const code = String(h.error_code || '').toUpperCase()
    if (code === 'HTTP_200' || /^HTTP_2\d{2}$/.test(code)) {
      return {
        ...h,
        search_blocked: false,
        error_code: null,
        banner_message: null,
        detail: null,
        insufficient_credits: false,
        rate_limited: false,
      }
    }
    const credits = h.remaining_credits
    const blockedCodes = new Set([
      'INSUFFICIENT_CREDITS',
      'RATE_LIMITED',
      'INVALID_API_KEY',
      'PLAN_REQUIRED',
    ])
    const reallyBlocked =
      credits === 0 ||
      blockedCodes.has(code) ||
      Boolean(h.insufficient_credits || h.rate_limited)
    if (!reallyBlocked && h.search_blocked) {
      return { ...h, search_blocked: false, banner_message: null, detail: null }
    }
    return h
  }, [pipeline?.prospeo_health, status?.prospeo_health])
  const prospeoEnrichBlocked = useMemo(() => {
    if (!prospeoHealth) return false
    const code = String(prospeoHealth.error_code || '').toUpperCase()
    if (code === 'HTTP_200' || /^HTTP_2\d{2}$/.test(code)) return false
    if (prospeoHealth.remaining_credits === 0) return true
    if (['INSUFFICIENT_CREDITS', 'RATE_LIMITED', 'INVALID_API_KEY', 'PLAN_REQUIRED'].includes(code)) {
      return true
    }
    return Boolean(prospeoHealth.insufficient_credits || prospeoHealth.rate_limited)
  }, [prospeoHealth])
  const showProspeoBlockedBanner = prospeoEnrichBlocked
  const enrichProgress = useMemo(() => {
    const ep = pipeline?.enrich_progress
    if (!ep || typeof ep !== 'object') return null
    const processed = Number(ep.processed) || 0
    const total = Number(ep.total) || 0
    if (total <= 0) return null
    return {
      processed,
      total,
      hasMore: Boolean(ep.has_more),
    }
  }, [pipeline?.enrich_progress])
  const enrichDisabledReason = useMemo(() => {
    if (freeze) return 'Panel congelado (solo lectura)'
    if (busy) return 'Otro paso del pipeline en curso'
    if (!prospeoConfigured) return 'Prospeo no está configurado (PROSPEO_API_KEY)'
    if (prospeoEnrichBlocked) return prospeoHealth?.banner_message || 'Prospeo sin créditos o limitado por plan'
    if (!hasEnrichTargets) return 'Primero buscá empresas ICP'
    return ''
  }, [freeze, busy, prospeoConfigured, prospeoEnrichBlocked, prospeoHealth, hasEnrichTargets])
  const directorySources = useMemo(
    () => companies.filter((c) => c.result_kind === 'directory_source'),
    [companies],
  )

  const loadStatus = useCallback(async () => {
    const statusUrl = resolveApiUrl('/lead-sourcing/status')
    if (import.meta.env.DEV) {
      console.log('[Lead Sourcing] GET status URL:', statusUrl)
    }
    setStatusLoading(true)
    setStatusError(null)
    try {
      const s = await fetchLeadSourcingStatus({ timeoutMs: STATUS_FETCH_TIMEOUT_MS })
      if (import.meta.env.DEV) {
        console.info('[Lead Sourcing] status OK:', statusUrl, s)
      }
      setStatus(s && typeof s === 'object' ? s : { configured: false })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setStatusError(msg)
      setStatus({ configured: false, message: msg, providers: [] })
      console.error('[Lead Sourcing] status failed:', msg)
    } finally {
      setStatusLoading(false)
    }
  }, [])

  const loadPipeline = useCallback(async () => {
    if (!campaignId || !Number.isFinite(Number(campaignId)) || Number(campaignId) < 1) {
      setPipeline(null)
      setPipelineError(null)
      setPipelineLoading(false)
      return
    }
    const pipelinePath = `/campaigns/${campaignId}/lead-sourcing/pipeline`
    const pipelineUrl = resolveApiUrl(pipelinePath)
    if (import.meta.env.DEV) {
      console.log('[Lead Sourcing] GET pipeline URL:', pipelineUrl, 'campaignId=', campaignId)
    }
    setPipelineLoading(true)
    setPipelineError(null)
    try {
      const p = await fetchLeadSourcingPipeline(campaignId, {
        timeoutMs: PIPELINE_FETCH_TIMEOUT_MS,
      })
      if (import.meta.env.DEV) {
        console.info('[Lead Sourcing] pipeline OK:', pipelineUrl, p)
      }
      setPipeline(p && typeof p === 'object' ? p : null)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setPipelineError(msg)
      console.error('[Lead Sourcing] pipeline load failed:', msg)
    } finally {
      setPipelineLoading(false)
    }
  }, [campaignId])

  const retryBootstrap = useCallback(async () => {
    setError(null)
    setStatusError(null)
    setPipelineError(null)
    await Promise.all([loadStatus(), loadPipeline()])
  }, [loadStatus, loadPipeline])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      await Promise.all([loadStatus(), loadPipeline()])
      if (cancelled) {
        return
      }
    })()
    return () => {
      cancelled = true
    }
  }, [campaignId, loadStatus, loadPipeline])

  useEffect(() => {
    if (!busy) {
      setElapsedSec(0)
      return undefined
    }
    const started = Date.now()
    const tick = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - started) / 1000))
    }, 1000)
    return () => clearInterval(tick)
  }, [busy])

  useEffect(() => {
    if (!busy || !campaignId) {
      return undefined
    }
    const poll = setInterval(() => {
      void loadPipeline()
    }, 4000)
    return () => clearInterval(poll)
  }, [busy, campaignId, loadPipeline])

  async function executeStep(step) {
    const timeoutMs = STEP_TIMEOUT_MS[step] ?? 120000
    const res = await runLeadSourcingPipeline(
      campaignId,
      {
        step,
        company_limit: 15,
        people_limit: 40,
        fit_threshold: pipeline?.fit_threshold ?? 70,
      },
      { timeoutMs },
    )
    if (import.meta.env.DEV) {
      console.info('[Lead Sourcing] run:', step, res)
    }
    if (res?.pipeline) {
      setPipeline(res.pipeline)
    } else {
      await loadPipeline()
    }
    if (!res?.ok) {
      throw new Error(res?.message || `El paso «${step}» falló.`)
    }
    return res
  }

  async function handleRun(step) {
    setBusy(step)
    setError(null)
    setImportMsg(null)
    setLastFailedStep('')
    try {
      await executeStep(step)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setLastFailedStep(step)
      await loadPipeline()
    } finally {
      setBusy('')
    }
  }

  async function handleRunFull() {
    setBusy('full')
    setError(null)
    setImportMsg(null)
    setLastFailedStep('')
    try {
      await executeStep('full')
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setLastFailedStep('full')
      await loadPipeline()
    } finally {
      setBusy('')
    }
  }

  function toggleSelect(id) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  async function handleImport() {
    const ids = [...selected]
    if (!ids.length) {
      return
    }
    setBusy('import')
    setError(null)
    try {
      const res = await importCampaignLeads(campaignId, ids)
      setImportMsg(
        `Importados: ${res.imported} · Duplicados: ${res.skipped_duplicates}` +
          (res.errors?.length ? ` · Avisos: ${res.errors.length}` : ''),
      )
      setSelected(new Set())
      await loadPipeline()
      onImported?.(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  const icpSummary = [campaign?.target_industry, campaign?.target_country, campaign?.target_role]
    .filter(Boolean)
    .join(' · ')

  const stageLogs = useMemo(() => {
    const raw = Array.isArray(pipeline?.stage_logs)
      ? pipeline.stage_logs.filter((log) => log && typeof log === 'object')
      : []
    if (showPhantomExperimental) {
      return raw
    }
    return raw.filter((log) => !isPhantomLogMessage(log))
  }, [pipeline?.stage_logs, showPhantomExperimental])
  const isRunningRemote = pipeline?.run_state?.running === true
  const displayStageRaw = busy
    ? {
        companies: 'searching_companies',
        prepare_phantom: 'companies_found',
        extract_companies: 'companies_found',
        people: 'leads_detected',
        enrich: 'enriching_contacts',
      }[busy] || currentStage
    : currentStage
  const displayStage =
    !showPhantomExperimental &&
    ['preparing_phantom', 'phantom_ready', 'extracting_people'].includes(displayStageRaw)
      ? 'companies_found'
      : displayStageRaw
  const displayIdx = PIPELINE_STEPS_MVP.findIndex((s) => s.key === displayStage)

  const panelErr = pipeline?.last_error || error
  const showPanelError =
    Boolean(panelErr) && (!isPhantomPanelError(panelErr) || showPhantomExperimental)

  return (
    <section className="rounded-xl border border-violet-100/90 bg-gradient-to-br from-violet-50/40 via-white to-zinc-50 p-4 shadow-md shadow-violet-900/5 ring-1 ring-violet-900/5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-900">Lead Sourcing Engine</h2>
          <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-zinc-600">
            ICP → Web Search → empresas con scoring → Prospeo selectivo → import a campaña y Nexus
            Outreach (email / LinkedIn asistido / WhatsApp).
          </p>
          {icpSummary ? (
            <p className="mt-1.5 text-[11px] font-medium text-violet-900/80">ICP: {icpSummary}</p>
          ) : null}
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${
            statusLoading
              ? 'bg-zinc-100 text-zinc-600'
              : pipelineReady
                ? 'bg-emerald-100 text-emerald-900 ring-1 ring-emerald-200'
                : 'bg-amber-100 text-amber-950 ring-1 ring-amber-200'
          }`}
        >
          {statusLoading ? 'Verificando…' : pipelineReady ? 'Pipeline listo' : 'Falta configurar'}
        </span>
      </div>

      {(statusLoading || pipelineLoading) && !statusError && !pipelineError ? (
        <p className="mt-3 text-xs text-zinc-500">
          Cargando proveedores y pipeline…
        </p>
      ) : null}

      {statusError || pipelineError ? (
        <div className="mt-3 rounded-lg border border-rose-300 bg-rose-50 px-3 py-2.5 text-xs text-rose-900">
          <p className="font-semibold">No se pudo cargar Lead Sourcing</p>
          {statusError ? <p className="mt-1">Status: {statusError}</p> : null}
          {pipelineError ? <p className="mt-1">Pipeline: {pipelineError}</p> : null}
          <p className="mt-1 text-[11px] text-rose-800">
            Verificá que el backend esté en marcha y que la campaña exista. El panel sigue
            disponible para reintentar.
          </p>
          <button
            type="button"
            className="mt-2 rounded-md bg-rose-100 px-3 py-1.5 text-[11px] font-semibold text-rose-950 hover:bg-rose-200 disabled:opacity-50"
            disabled={statusLoading || pipelineLoading || Boolean(busy)}
            onClick={() => void retryBootstrap()}
          >
            {statusLoading || pipelineLoading ? 'Cargando…' : 'Reintentar carga'}
          </button>
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-1 text-[10px] font-medium text-violet-900/90">
        {(status?.pipeline?.length ? status.pipeline : FLOW).map((label, i) => (
          <span key={label} className="flex items-center gap-1">
            {i > 0 ? <span className="text-violet-300">→</span> : null}
            <span className="rounded bg-white/80 px-1.5 py-0.5 ring-1 ring-violet-100">{label}</span>
          </span>
        ))}
      </div>

      {!statusLoading && status?.providers?.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {status.providers.map((p) => {
            const optional = p.name === 'phantombuster'
            const required = !optional
            const ok = p.configured
            return (
              <span
                key={p.name}
                className={`rounded-md px-2 py-1 text-[10px] font-semibold ${
                  ok
                    ? 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-100'
                    : required
                      ? 'bg-amber-50 text-amber-900 ring-1 ring-amber-200'
                      : 'bg-zinc-100 text-zinc-500 ring-1 ring-zinc-200'
                }`}
                title={p.message}
              >
                {PROVIDER_LABELS[p.name] || p.name}
                {optional ? (ok ? ' (exp.)' : ' (opc.)') : ok ? ' ✅' : ' —'}
              </span>
            )
          })}
        </div>
      ) : null}

      {showProspeoBlockedBanner ? (
        <div className="mt-3 rounded-lg border border-rose-300 bg-rose-50 px-3 py-2.5 text-xs text-rose-950">
          <p className="font-semibold">
            {prospeoHealth?.banner_message || 'Prospeo sin créditos o limitado por plan'}
          </p>
          <p className="mt-1 text-[11px] text-rose-900">
            {prospeoHealth?.detail || 'Prospeo no pudo ejecutar búsqueda.'}
            {prospeoHealth?.error_code ? ` · Código: ${prospeoHealth.error_code}` : ''}
            {prospeoHealth?.current_plan ? ` · Plan: ${prospeoHealth.current_plan}` : ''}
            {prospeoHealth?.remaining_credits != null
              ? ` · Créditos: ${prospeoHealth.remaining_credits}`
              : ''}
          </p>
        </div>
      ) : null}

      <div className="mt-4 grid gap-1 sm:grid-cols-3 lg:grid-cols-5">
        {PIPELINE_STEPS_MVP.map((s, i) => {
          const done = displayIdx >= 0 && i < displayIdx && currentStage !== 'error'
          const active = s.key === displayStage
          return (
            <div
              key={s.key}
              className={`rounded-lg border px-2 py-1.5 text-center text-[10px] font-medium ${
                active
                  ? 'border-violet-400 bg-violet-100 text-violet-950'
                  : done
                    ? 'border-emerald-200 bg-emerald-50/80 text-emerald-900'
                    : 'border-zinc-200 bg-white text-zinc-500'
              }`}
            >
              {s.label}
            </div>
          )
        })}
      </div>

      {busy ? (
        <p className="mt-3 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-900">
          <span className="font-semibold">
            {STEP_LABELS[busy] || busy} en curso… {elapsedSec}s
          </span>
          {isRunningRemote ? (
            <span className="ml-2 text-violet-700">(backend confirmó inicio)</span>
          ) : (
            <span className="ml-2 text-violet-700">(esperando backend…)</span>
          )}
        </p>
      ) : null}

      {showPanelError ? (
        <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          <p>{panelErr}</p>
          {lastFailedStep ? (
            <button
              type="button"
              className="mt-2 rounded-md bg-rose-100 px-2.5 py-1 text-[11px] font-semibold text-rose-900 hover:bg-rose-200"
              disabled={Boolean(busy)}
              onClick={() =>
                void (lastFailedStep === 'full' ? handleRunFull() : handleRun(lastFailedStep))
              }
            >
              Reintentar {lastFailedStep === 'full' ? 'pipeline' : `paso ${lastFailedStep}`}
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <PremiumGradientButton
          className="px-3 py-2 text-xs"
          disabled={freeze || !pipelineReady || Boolean(busy)}
          onClick={() => void handleRun('companies')}
        >
          {busy === 'companies' ? 'Buscando empresas…' : '1. Buscar empresas ICP'}
        </PremiumGradientButton>
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={Boolean(enrichDisabledReason)}
              title={
                enrichDisabledReason === 'Primero buscá empresas ICP'
                  ? enrichDisabledReason
                  : undefined
              }
              className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs font-semibold text-zinc-800 hover:bg-zinc-50 disabled:opacity-40"
              onClick={() => void handleRun('enrich')}
            >
              {busy === 'enrich'
                ? 'Enriqueciendo…'
                : enrichProgress?.hasMore
                  ? '2. Enriquecer siguientes (Prospeo)'
                  : '2. Enriquecer contactos (Prospeo)'}
            </button>
            {enrichProgress ? (
              <span className="text-[10px] font-medium text-violet-800">
                Procesadas {enrichProgress.processed} de {enrichProgress.total}
                {enrichProgress.hasMore ? ' · quedan más' : ' · completo'}
              </span>
            ) : null}
          </div>
          {enrichDisabledReason ? (
            <p className="max-w-xs text-[10px] text-amber-800">
              Deshabilitado porque: {enrichDisabledReason}
            </p>
          ) : null}
        </div>
        <PremiumGradientButton
          className="px-3 py-2 text-xs"
          disabled={freeze || !pipelineReady || Boolean(busy)}
          onClick={() => void handleRunFull()}
        >
          {busy === 'full'
            ? 'Pipeline MVP…'
            : busy && ['companies', 'enrich'].includes(busy)
              ? `${STEP_LABELS[busy] || busy}…`
              : 'Pipeline MVP (Web Search + Prospeo)'}
        </PremiumGradientButton>
      </div>

      <details
        className="mt-3 rounded-lg border border-dashed border-zinc-300 bg-zinc-50/80 p-3"
        open={showPhantomExperimental}
        onToggle={(e) => setShowPhantomExperimental(e.target.open)}
      >
        <summary className="cursor-pointer text-xs font-semibold text-zinc-700">
          Modo avanzado — PhantomBuster (experimental)
          {phantomExperimentalAvailable ? (
            <span className="ml-2 font-normal text-emerald-700">configurado</span>
          ) : (
            <span className="ml-2 font-normal text-zinc-500">opcional</span>
          )}
        </summary>
        <p className="mt-2 text-[10px] text-zinc-600">
          Extracción asistida de personas en LinkedIn. No es necesaria para el flujo comercial MVP.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={
              freeze ||
              Boolean(busy) ||
              (companyCandidates.length === 0 && directorySources.length === 0)
            }
            className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-xs font-semibold text-violet-900 hover:bg-violet-50 disabled:opacity-40"
            onClick={() => void handleRun('prepare_phantom')}
          >
            {busy === 'prepare_phantom' ? 'Preparando…' : 'Preparar cola Phantom'}
          </button>
          <button
            type="button"
            disabled={freeze || Boolean(busy)}
            className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-xs font-semibold text-violet-900 hover:bg-violet-50 disabled:opacity-40"
            onClick={() => void handleRun('people')}
          >
            {busy === 'people' ? 'Extrayendo…' : 'Extraer personas (Phantom)'}
          </button>
        </div>
      </details>

      <p className="mt-2 text-[10px] text-zinc-500">
        Prospeo enriquece leads con email/teléfono cuando hay datos. Empresas ICP:{' '}
        {pipeline?.companies_count ?? companyCandidates.length} · Cuentas/leads:{' '}
        {realPeople.length} contactos · {pipeline?.ready_count ?? importable.length} listos outreach
        {(pipeline?.icp_target_phrase || pipeline?.search_query || pipeline?.google_query)
          ? ` · ICP target: ${pipeline.icp_target_phrase || pipeline.search_query || pipeline.google_query}`
          : ''}
      </p>

      {stageLogs.length > 0 ? (
        <div className="mt-3 rounded-lg border border-zinc-200 bg-zinc-50/80 p-3">
          <p className="text-xs font-semibold text-zinc-800">Logs del pipeline</p>
          <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-[10px] text-zinc-700">
            {[...stageLogs].reverse().slice(0, 12).map((log, idx) => (
              <li key={`${log.at}-${log.step}-${idx}`} className="flex flex-wrap items-center gap-1.5">
                <span className={`rounded px-1 py-0.5 font-bold ${eventBadgeClass(log.event)}`}>
                  {log.event}
                </span>
                <span className="font-medium text-zinc-900">{STEP_LABELS[log.step] || log.step}</span>
                {log.duration_ms != null ? (
                  <span className="text-zinc-500">{formatDuration(log.duration_ms)}</span>
                ) : null}
                {log.result_count != null ? (
                  <span className="text-zinc-500">{log.result_count} resultados</span>
                ) : null}
                {log.message ? <span className="text-zinc-600">{log.message}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {pipeline?.extraction_stats?.sources?.length ? (
        <div className="mt-3 rounded-lg border border-sky-200 bg-sky-50/40 p-3">
          <p className="text-xs font-semibold text-sky-950">
            Fuentes directorio — {directorySources.length} semillas
            {showPhantomExperimental && pipeline.blocked_sources_count
              ? ` · ${pipeline.blocked_sources_count} vía Phantom (exp.)`
              : ''}
          </p>
          <ul className="mt-2 max-h-32 space-y-1.5 overflow-y-auto text-[11px] text-zinc-700">
            {pipeline.extraction_stats.sources.map((s, idx) => {
              const blocked = s.status === 'requires_phantombuster'
              return (
              <li key={`${s.directory_url}-${idx}`} className="flex flex-wrap items-center gap-1.5">
                <span className="rounded bg-white px-1.5 py-0.5 font-medium text-sky-900 ring-1 ring-sky-100">
                  {s.platform || 'fuente'}
                </span>
                {blocked && showPhantomExperimental ? (
                  <span className="rounded bg-amber-100 px-1 py-0.5 text-[9px] font-bold text-amber-900">
                    Modo Phantom (exp.)
                  </span>
                ) : blocked ? (
                  <span className="rounded bg-sky-100 px-1 py-0.5 text-[9px] font-bold text-sky-900">
                    Directorio ICP
                  </span>
                ) : (
                  <span className="rounded bg-emerald-100 px-1 py-0.5 text-[9px] font-bold text-emerald-900">
                    OK
                  </span>
                )}
                <span className="font-medium text-zinc-900">{s.name || s.platform}</span>
                {s.message ? (
                  <span className="text-[10px] text-zinc-600">{s.message}</span>
                ) : (
                  <span className="truncate text-zinc-500">{s.directory_url}</span>
                )}
              </li>
              )
            })}
          </ul>
        </div>
      ) : null}

      {showPhantomExperimental && pipeline?.phantom_queue?.items?.length ? (
        <div className="mt-3 rounded-lg border border-violet-200 bg-violet-50/40 p-3">
          <p className="text-xs font-semibold text-violet-950">
            Cola PhantomBuster ({pipeline.phantom_queue.total_items} items)
          </p>
          <p className="mt-0.5 text-[10px] text-violet-900/80">
            ICP: {pipeline.phantom_queue.icp_target_phrase || '—'} · Rol:{' '}
            {pipeline.phantom_queue.role_hint || '—'}
          </p>
          <ul className="mt-2 max-h-28 space-y-1 overflow-y-auto text-[11px] text-zinc-600">
            {pipeline.phantom_queue.items.slice(0, 10).map((item, idx) => (
              <li key={`${item.external_id || item.url}-${idx}`}>
                <span className="font-medium text-zinc-800">{item.kind}</span> · {item.name}
                {item.url ? ` · ${item.url}` : ''}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {showPhantomExperimental && pipeline?.phantom_debug ? (
        <div
          className={`mt-3 rounded-lg border p-3 ${
            pipeline.phantom_debug.leads_count > 0
              ? 'border-emerald-200 bg-emerald-50/40'
              : pipeline.phantom_debug.outcome === 'missing_session' ||
                  pipeline.phantom_debug.outcome === 'missing_search_input'
                ? 'border-amber-300 bg-amber-50/60'
                : 'border-sky-200 bg-sky-50/40'
          }`}
        >
          <p className="text-xs font-semibold text-zinc-900">Debug PhantomBuster</p>
          <p className="mt-1 text-[11px] text-zinc-700">
            {pipeline.phantom_debug.outcome_message || 'Sin mensaje de diagnóstico.'}
          </p>
          {pipeline.phantom_debug.user_action ? (
            <p className="mt-1 text-[11px] font-medium text-amber-900">
              {pipeline.phantom_debug.user_action}
            </p>
          ) : null}
          <ul className="mt-2 space-y-0.5 text-[10px] text-zinc-600">
            <li>Agent ID: {pipeline.phantom_debug.agent_id || '—'}</li>
            <li>Agente: {pipeline.phantom_debug.agent_name || '—'}</li>
            {pipeline.phantom_debug.auth_debug ? (
              <li>
                Auth: {pipeline.phantom_debug.auth_debug.auth_header || '—'} · key{' '}
                {pipeline.phantom_debug.auth_debug.api_key_present ? 'presente' : 'ausente'} · len{' '}
                {pipeline.phantom_debug.auth_debug.api_key_length ?? '—'}
              </li>
            ) : null}
            <li>Container: {pipeline.phantom_debug.container_id || '—'}</li>
            <li>Launch ID: {pipeline.phantom_debug.launch_id || pipeline.phantom_debug.container_id || '—'}</li>
            <li>Status: {pipeline.phantom_debug.container_status || '—'}</li>
            {pipeline.phantom_debug.poll_iterations != null ? (
              <li>
                Poll: {pipeline.phantom_debug.poll_iterations} iter ·{' '}
                {pipeline.phantom_debug.poll_elapsed_sec ?? '—'}s · break:{' '}
                {pipeline.phantom_debug.poll_break || '—'}
                {pipeline.phantom_debug.container_poll_timeout ? ' · timeout' : ''}
              </li>
            ) : null}
            {pipeline.phantom_debug.step_completion ? (
              <li>Paso: {pipeline.phantom_debug.step_completion}</li>
            ) : null}
            <li>
              Launch:{' '}
              {pipeline.phantom_debug.launch_uses_saved_agent_config
                ? 'config guardada del agente'
                : 'input generado por Nexus'}
            </li>
            {pipeline.phantom_debug.output_source ? (
              <li>Output source: {pipeline.phantom_debug.output_source}</li>
            ) : null}
            {pipeline.phantom_debug.output_endpoint ? (
              <li>Output endpoint: {pipeline.phantom_debug.output_endpoint}</li>
            ) : null}
            {pipeline.phantom_debug.leads_list_id ? (
              <li>Leads list ID: {pipeline.phantom_debug.leads_list_id}</li>
            ) : null}
            {pipeline.phantom_debug.s3_folders &&
            typeof pipeline.phantom_debug.s3_folders === 'object' &&
            !Array.isArray(pipeline.phantom_debug.s3_folders) ? (
              <li>
                S3: {pipeline.phantom_debug.s3_folders.orgS3Folder || '—'} /{' '}
                {pipeline.phantom_debug.s3_folders.s3Folder || '—'}
              </li>
            ) : null}
            {pipeline.phantom_debug.has_result_object != null ? (
              <li>Result object API: {pipeline.phantom_debug.has_result_object ? 'sí' : 'no'}</li>
            ) : null}
            <li>
              Sesión LinkedIn en agente:{' '}
              {pipeline.phantom_debug.session_cookie_in_agent === true
                ? 'detectada'
                : pipeline.phantom_debug.session_cookie_in_agent === false
                  ? 'no detectada'
                  : '—'}
            </li>
            {pipeline.phantom_debug.linkedin_query_exact ? (
              <li className="break-all">
                Query Phantom: {pipeline.phantom_debug.linkedin_query_exact}
              </li>
            ) : null}
            <li>
              Personas: {pipeline.phantom_debug.leads_count ?? 0} · Filas parseadas:{' '}
              {pipeline.phantom_debug.rows_parsed ?? 0}
            </li>
            <li>
              Raw: {pipeline.phantom_debug.raw_rows_count ?? pipeline.phantom_debug.rows_parsed ?? 0} ·
              válidas: {pipeline.phantom_debug.valid_rows_count ?? pipeline.phantom_debug.leads_count ?? 0} ·
              descartadas: {pipeline.phantom_debug.discarded_rows_count ?? 0}
            </li>
            {pipeline.phantom_debug.parse_note ? (
              <li>Parse: {pipeline.phantom_debug.parse_note}</li>
            ) : null}
            {pipeline.phantom_debug.first_row_keys?.length ? (
              <li>First row keys: {pipeline.phantom_debug.first_row_keys.join(', ')}</li>
            ) : null}
          </ul>
          {pipeline.phantom_debug.first_row_sample ? (
            <details className="mt-2 text-[10px] text-zinc-600">
              <summary className="cursor-pointer font-medium text-zinc-800">
                Primera fila parseada
              </summary>
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-[9px]">
                {JSON.stringify(pipeline.phantom_debug.first_row_sample, null, 2)}
              </pre>
            </details>
          ) : null}
          {pipeline.phantom_debug.discarded_rows_sample?.length ? (
            <details className="mt-2 text-[10px] text-zinc-600">
              <summary className="cursor-pointer font-medium text-zinc-800">
                Filas descartadas
              </summary>
              <pre className="mt-1 max-h-36 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-[9px]">
                {JSON.stringify(pipeline.phantom_debug.discarded_rows_sample, null, 2)}
              </pre>
            </details>
          ) : null}
          {pipeline.phantom_debug.input_summary ? (
            <details className="mt-2 text-[10px] text-zinc-600">
              <summary className="cursor-pointer font-medium text-zinc-800">Payload enviado al launch</summary>
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-[9px]">
                {JSON.stringify(
                  pipeline.phantom_debug.launch_payload_sent ||
                    { id: pipeline.phantom_debug.agent_id, argument: pipeline.phantom_debug.argument_sent },
                  null,
                  2,
                )}
              </pre>
            </details>
          ) : null}
          {pipeline.phantom_debug.poll_trace?.length ? (
            <details className="mt-2 text-[10px] text-zinc-600">
              <summary className="cursor-pointer font-medium text-zinc-800">
                Polling container (iteraciones / status)
              </summary>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-[9px]">
                {JSON.stringify(pipeline.phantom_debug.poll_trace, null, 2)}
              </pre>
            </details>
          ) : null}
          {pipeline.phantom_debug.output_attempts?.length ? (
            <details className="mt-2 text-[10px] text-zinc-600">
              <summary className="cursor-pointer font-medium text-zinc-800">
                Fuentes intentadas (S3 / result-object / logs)
              </summary>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-[9px]">
                {JSON.stringify(pipeline.phantom_debug.output_attempts, null, 2)}
              </pre>
            </details>
          ) : null}
          {pipeline.phantom_debug.output_preview ? (
            <details className="mt-2 text-[10px] text-zinc-600">
              <summary className="cursor-pointer font-medium text-zinc-800">Output recibido</summary>
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-[9px]">
                {pipeline.phantom_debug.output_preview}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}

      {pipelineLoading ? (
        <p className="mt-2 text-xs text-zinc-500">Cargando pipeline de campaña…</p>
      ) : null}
      {!pipeline && !pipelineLoading && campaignId && !pipelineError ? (
        <p className="mt-2 text-xs text-zinc-500">Pipeline vacío — podés iniciar búsqueda de empresas.</p>
      ) : null}

      <MvpCompanyDomainsTable
        companies={companyCandidates}
        metrics={pipeline?.domain_resolution_metrics}
      />

      {directorySources.length > 0 ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50/40 p-3">
          <p className="text-xs font-semibold text-amber-950">
            Fuentes semilla ({directorySources.length}) — Wellfound/G2/Clutch no se crawlean directo
          </p>
          <p className="mt-0.5 text-[10px] text-amber-900/80">
            Referencia para outreach manual o modo experimental Phantom.
          </p>
          <ul className="mt-2 max-h-28 space-y-1 overflow-y-auto text-[11px] text-zinc-600">
            {directorySources.slice(0, 8).map((c) => (
              <li key={c.external_id}>
                {c.name}
                {c.website_url ? ` · ${c.website_url}` : ''}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {showPhantomExperimental && pipeline?.phantom_debug?.phantom_test_mode ? (
        <p className="mt-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-[11px] font-medium text-sky-950">
          Modo test Phantom activo — 1 query, timeout ~30s, máx. 25 perfiles.
        </p>
      ) : null}

      {showPhantomExperimental &&
      (pipeline?.phantom_debug?.company_searches?.length ||
        pipeline?.phantom_debug?.input_summary?.company_searches?.length) ? (
        <div className="mt-3 rounded-lg border border-violet-100 bg-violet-50/60 px-3 py-2 text-[11px] text-violet-950">
          <p className="font-semibold">
            {pipeline?.phantom_debug?.phantom_test_mode
              ? 'Query Phantom (test)'
              : 'Búsquedas Phantom (por empresa)'}
          </p>
          <ul className="mt-1 max-h-32 space-y-1 overflow-y-auto">
            {(pipeline.phantom_debug.company_searches ||
              pipeline.phantom_debug.input_summary?.company_searches ||
              []).map((s) => (
              <li key={`${s.company}-${s.role_term}`} className="break-all">
                <span className="font-medium">{s.company}</span>
                <code className="ml-1 block text-[10px]">
                  {s.linkedin_keywords || s.site_query}
                </code>
              </li>
            ))}
          </ul>
        </div>
      ) : showPhantomExperimental && pipeline?.phantom_debug?.linkedin_query_exact ? (
        <p className="mt-3 rounded-lg border border-violet-100 bg-violet-50/60 px-3 py-2 text-[11px] text-violet-950">
          <span className="font-semibold">Query PhantomBuster: </span>
          <code className="break-all">{pipeline.phantom_debug.linkedin_query_exact}</code>
        </p>
      ) : null}

      <ProspectingLeadsTable
        rows={pipeline?.prospecting_leads || []}
        phoneInfo={pipeline?.prospeo_phone_info}
        selectedId={outreachSelectedId}
        onSelectRow={setOutreachSelectedId}
      />

      <MvpContactsTable
        rows={pipeline?.company_contacts || []}
        metrics={pipeline?.mvp_contact_metrics}
        selectedId={outreachSelectedId}
        onSelectContact={setOutreachSelectedId}
      />
      <ProspeoSearchDebugPanel rows={pipeline?.prospeo_search_debug || []} />
      <ProspeoContactDebugPanel rows={pipeline?.prospeo_contact_debug || []} />

      {realPeople.length > 0 ? (
        <div className="mt-4 overflow-hidden rounded-xl border border-zinc-200/90 bg-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-100 bg-zinc-50/80 px-3 py-2">
            <span className="text-xs font-semibold text-zinc-800">Personas (contactos reales)</span>
            <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-zinc-600">
              <input
                type="checkbox"
                checked={showDiscarded}
                onChange={(e) => setShowDiscarded(e.target.checked)}
              />
              Mostrar descartados
            </label>
            {importable.length > 0 ? (
              <button
                type="button"
                className="text-[11px] font-semibold text-violet-800"
                onClick={() => {
                  setSelected(new Set(importable.map((p) => p.external_id)))
                }}
              >
                Seleccionar listos ({importable.length})
              </button>
            ) : null}
          </div>
          <div className="grid grid-cols-[1.2fr_1.4fr_1fr_0.7fr_0.45fr] gap-2 border-b border-zinc-100 bg-white px-3 py-2 text-[10px] font-bold uppercase tracking-wide text-zinc-500">
            <span>Nombre</span>
            <span>Cargo / headline</span>
            <span>Empresa</span>
            <span>LinkedIn</span>
            <span className="text-right">ICP</span>
          </div>
          <ul className="max-h-80 divide-y divide-zinc-100 overflow-y-auto">
            {realPeople.map((p) => {
              const canImport = isOutreachReady(p) && !p.already_in_campaign
              return (
                <li key={p.external_id} className="grid grid-cols-[auto_1.2fr_1.4fr_1fr_0.7fr_0.45fr] gap-2 px-3 py-2 text-xs">
                  <input
                    type="checkbox"
                    className="mt-1"
                    disabled={!canImport || freeze}
                    checked={selected.has(p.external_id)}
                    onChange={() => toggleSelect(p.external_id)}
                  />
                  <span className="min-w-0 font-semibold text-zinc-900">{p.name}</span>
                  <span className="min-w-0 text-zinc-600">
                    {p.role || '—'}
                    {p.enrichment_confidence != null ? (
                      <span className="ml-1 text-[10px] text-violet-700">· {p.enrichment_confidence}%</span>
                    ) : null}
                  </span>
                  <span className="min-w-0 text-zinc-700" title={p.matched_icp_company || undefined}>
                    {p.company_name || '—'}
                    {p.matched_icp_company && p.matched_icp_company !== p.company_name ? (
                      <span className="block text-[10px] text-violet-700">
                        ICP: {p.matched_icp_company}
                        {p.company_match_ratio != null
                          ? ` (${Math.round(p.company_match_ratio * 100)}%)`
                          : ''}
                      </span>
                    ) : null}
                  </span>
                  <span className="min-w-0">
                    {p.linkedin_url ? (
                      <a
                        href={p.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-semibold text-sky-700 hover:text-sky-900"
                      >
                        LinkedIn
                      </a>
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </span>
                  <span className="text-right">
                    {p.compatibility_score != null ? (
                      <span
                        className={`inline-flex flex-col items-end gap-0.5 ${
                          p.fit_tier === 'low_fit' ? '' : ''
                        }`}
                        title={p.score_breakdown || undefined}
                      >
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                            p.fit_tier === 'low_fit'
                              ? 'bg-amber-100 text-amber-950 ring-1 ring-amber-200'
                              : 'bg-violet-100 text-violet-900'
                          }`}
                        >
                          {p.compatibility_score}%
                        </span>
                        {p.fit_tier === 'low_fit' ? (
                          <span className="text-[9px] font-semibold uppercase text-amber-800">
                            Bajo fit
                          </span>
                        ) : null}
                      </span>
                    ) : (
                      '—'
                    )}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}

      {showDiscarded && (pipeline?.discarded_leads?.length || pipeline?.lead_score_audit?.length) ? (
        <div className="mt-4 rounded-xl border border-amber-200/90 bg-amber-50/40 p-3">
          <p className="text-xs font-semibold text-amber-950">
            Descartados / auditoría de scoring
            {pipeline?.display_min_score != null
              ? ` · etiqueta bajo fit desde ${pipeline.display_min_score}%`
              : ''}
          </p>
          {pipeline.lead_score_audit?.length ? (
            <details className="mt-2 text-[11px] text-amber-950" open>
              <summary className="cursor-pointer font-medium">Score breakdown por lead</summary>
              <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto">
                {pipeline.lead_score_audit.map((a) => (
                  <li key={a.external_id} className="rounded bg-white/70 px-2 py-1">
                    <span className="font-semibold">{a.name}</span> — {a.compatibility_score}% (
                    {a.fit_tier})
                    {a.score_breakdown ? (
                      <span className="block text-[10px] text-amber-900/90">{a.score_breakdown}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {showPhantomExperimental && pipeline.phantom_debug?.company_match_audit?.length ? (
            <details className="mt-2 text-[11px] text-amber-950">
              <summary className="cursor-pointer font-medium">
                Coincidencia empresa / lead ({pipeline.phantom_debug.company_match_audit.length})
              </summary>
              <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto">
                {pipeline.phantom_debug.company_match_audit.map((m) => (
                  <li key={`${m.name}-${m.lead_company}`} className="rounded bg-white/70 px-2 py-1">
                    {m.name} @ {m.lead_company || '—'} → {m.matched_icp_company || '—'} (
                    {m.passed ? 'OK' : 'NO'}) {m.match_note}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {pipeline.discarded_leads?.length ? (
            <details className="mt-2 text-[11px] text-amber-950">
              <summary className="cursor-pointer font-medium">
                Filas descartadas en parse ({pipeline.discarded_leads.length})
              </summary>
              <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto">
                {pipeline.discarded_leads.map((d, i) => (
                  <li key={`${d.reason}-${d.name}-${i}`} className="rounded bg-white/70 px-2 py-1">
                    <span className="font-semibold">{d.name || '—'}</span>
                    <span className="text-amber-800"> · {d.reason}</span>
                    {d.score_breakdown ? (
                      <span className="block text-[10px]">{d.score_breakdown}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}

      <MvpOutreachWorkspace
        campaignId={campaignId}
        profiles={pipeline?.lead_profiles || []}
        prospectingRows={pipeline?.prospecting_leads || []}
        freeze={freeze}
        onPipelineUpdate={setPipeline}
        initialSelectedId={outreachSelectedId}
        onSelectLead={setOutreachSelectedId}
      />

      {selected.size > 0 ? (
        <button
          type="button"
          disabled={freeze || busy === 'import'}
          className="mt-3 w-full rounded-lg border border-emerald-300 bg-emerald-50 py-2.5 text-sm font-semibold text-emerald-900 hover:bg-emerald-100 disabled:opacity-40"
          onClick={() => void handleImport()}
        >
          {busy === 'import' ? 'Importando…' : `Importar a campaña (${selected.size})`}
        </button>
      ) : null}

      {importMsg ? <p className="mt-2 text-xs font-medium text-emerald-800">{importMsg}</p> : null}

      {!people.length && !pipelineLoading && pipelineReady ? (
        <p className="mt-4 text-center text-xs text-zinc-500">
          Ejecutá el pipeline para cargar empresas y personas reales.
        </p>
      ) : null}
    </section>
  )
}
