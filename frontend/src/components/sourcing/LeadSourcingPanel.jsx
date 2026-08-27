import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LeadSourcingErrorBoundary } from './LeadSourcingErrorBoundary.jsx'
import {
  fetchLeadSourcingPipeline,
  fetchLeadSourcingStatus,
  importCampaignLeads,
  runLeadSourcingPipeline,
} from '../../utils/api.js'
import { hasRealLinkedInUrl } from '../../utils/linkedinAssist.js'
import { MvpOutreachWorkspace } from './MvpOutreachWorkspace.jsx'
import { ProspectingLeadsTable } from './ProspectingLeadsTable.jsx'
import { showOpsDebug } from '../../utils/opsDebug.js'
import {
  MvpCompanyDomainsTable,
} from './MvpCompanyDomainsTable.jsx'
import {
  MvpContactsTable,
  ProspeoContactDebugPanel,
  ProspeoSearchDebugPanel,
} from './MvpContactsTable.jsx'

const STAGE_LABELS = {
  idle: 'Listo',
  searching_companies: 'Buscando empresas…',
  searching_people: 'Buscando personas…',
  companies_found: 'Empresas encontradas',
  leads_detected: 'Detectando contactos…',
  enriching_contacts: 'Enriqueciendo con Prospeo…',
  ready_to_import: 'Prospectos listos',
  error: 'Error',
}

const STATUS_FETCH_TIMEOUT_MS = 10000
const PIPELINE_FETCH_TIMEOUT_MS = 15000
const FULL_TIMEOUT_MS = 120000

/**
 * Búsqueda automática de prospectos: un clic / auto-run → import a campaña.
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
  const [importMsg, setImportMsg] = useState(null)
  const [elapsedSec, setElapsedSec] = useState(0)
  const [outreachSelectedId, setOutreachSelectedId] = useState('')
  const autoStartedRef = useRef(false)
  const autoImportedRef = useRef(false)

  const isB2c = campaign?.outreach_mode === 'b2c'
  const pipelineReady = useMemo(() => {
    if (isB2c) {
      const providers = Array.isArray(status?.providers) ? status.providers : []
      return providers.some((p) => p?.name === 'prospeo' && p?.configured)
    }
    return status?.configured === true
  }, [isB2c, status?.configured, status?.providers])
  const currentStage = pipeline?.stage || 'idle'

  const people = useMemo(
    () =>
      Array.isArray(pipeline?.people)
        ? pipeline.people.filter((p) => p && typeof p === 'object')
        : [],
    [pipeline],
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
      const hasName = Boolean((p.name || '').trim())
      const hasLi = hasRealLinkedInUrl(p.linkedin_url)
      const hasEmail = (p.email || '').includes('@')
      // B2C: con LinkedIn o email alcanza para importar; B2B mantiene barra alta.
      if (campaign?.outreach_mode === 'b2c') {
        return hasName && (hasLi || hasEmail)
      }
      return hasName && hasLi && hasEmail
    },
    [prospectingById, campaign?.outreach_mode],
  )

  const importable = useMemo(
    () => people.filter((p) => isOutreachReady(p) && !p.already_in_campaign),
    [people, isOutreachReady],
  )

  const readyCount =
    pipeline?.ready_count ??
    (Array.isArray(pipeline?.prospecting_leads)
      ? pipeline.prospecting_leads.filter((r) => r?.outreach_ready).length
      : importable.length)

  const importedCount = useMemo(
    () => people.filter((p) => p.already_in_campaign).length,
    [people],
  )

  const companyCandidates = useMemo(() => {
    const companies = Array.isArray(pipeline?.companies) ? pipeline.companies : []
    return companies.filter((c) => (c.result_kind || 'company') === 'company')
  }, [pipeline])

  const prospeoHealth = useMemo(() => {
    const live = status?.prospeo_health
    const stored = pipeline?.prospeo_health
    const h = live && typeof live === 'object' ? live : stored
    if (!h || typeof h !== 'object') return null
    return h
  }, [pipeline?.prospeo_health, status?.prospeo_health])

  const prospeoBlocked = useMemo(() => {
    if (!prospeoHealth) return false
    const code = String(prospeoHealth.error_code || '').toUpperCase()
    if (code === 'HTTP_200' || /^HTTP_2\d{2}$/.test(code)) return false
    if (prospeoHealth.remaining_credits === 0) return true
    return ['INSUFFICIENT_CREDITS', 'RATE_LIMITED', 'INVALID_API_KEY', 'PLAN_REQUIRED'].includes(code)
  }, [prospeoHealth])

  const loadStatus = useCallback(async () => {
    setStatusLoading(true)
    setStatusError(null)
    try {
      const s = await fetchLeadSourcingStatus({ timeoutMs: STATUS_FETCH_TIMEOUT_MS })
      setStatus(s && typeof s === 'object' ? s : { configured: false })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setStatusError(msg)
      setStatus({ configured: false, message: msg, providers: [] })
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
    setPipelineLoading(true)
    setPipelineError(null)
    try {
      const p = await fetchLeadSourcingPipeline(campaignId, {
        timeoutMs: PIPELINE_FETCH_TIMEOUT_MS,
      })
      setPipeline(p && typeof p === 'object' ? p : null)
    } catch (e) {
      setPipelineError(e instanceof Error ? e.message : String(e))
    } finally {
      setPipelineLoading(false)
    }
  }, [campaignId])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      await Promise.all([loadStatus(), loadPipeline()])
      if (cancelled) return
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
    if (!busy || !campaignId) return undefined
    const poll = setInterval(() => {
      void loadPipeline()
    }, 4000)
    return () => clearInterval(poll)
  }, [busy, campaignId, loadPipeline])

  const autoImportReady = useCallback(
    async (pipe) => {
      if (freeze || autoImportedRef.current) return
      const list = Array.isArray(pipe?.people) ? pipe.people : people
      const byProspecting = new Map()
      for (const row of pipe?.prospecting_leads || pipeline?.prospecting_leads || []) {
        if (row?.external_id) byProspecting.set(row.external_id, row)
      }
      const ids = list
        .filter((p) => {
          if (!p?.external_id || p.already_in_campaign) return false
          const row = byProspecting.get(p.external_id)
          if (row) return Boolean(row.outreach_ready)
          return (
            Boolean((p.name || '').trim()) &&
            hasRealLinkedInUrl(p.linkedin_url) &&
            (p.email || '').includes('@')
          )
        })
        .map((p) => p.external_id)
      if (!ids.length) return

      autoImportedRef.current = true
      setBusy('import')
      setError(null)
      try {
        const res = await importCampaignLeads(campaignId, ids)
        setImportMsg(
          `Se importaron ${res.imported} prospectos a la campaña` +
            (res.skipped_duplicates ? ` · ${res.skipped_duplicates} ya estaban` : ''),
        )
        await loadPipeline()
        onImported?.(res)
      } catch (e) {
        autoImportedRef.current = false
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy('')
      }
    },
    [campaignId, freeze, loadPipeline, onImported, people, pipeline?.prospecting_leads],
  )

  const runFullPipeline = useCallback(async () => {
    if (freeze || !campaignId || !pipelineReady) return
    setBusy('full')
    setError(null)
    setImportMsg(null)
    autoImportedRef.current = false
    try {
      const res = await runLeadSourcingPipeline(
        campaignId,
        {
          step: 'full',
          company_limit: 15,
          people_limit: 40,
          fit_threshold: pipeline?.fit_threshold ?? 70,
        },
        { timeoutMs: FULL_TIMEOUT_MS },
      )
      if (res?.pipeline) {
        setPipeline(res.pipeline)
      } else {
        await loadPipeline()
      }
      if (!res?.ok) {
        throw new Error(res?.message || 'La búsqueda de prospectos falló.')
      }
      await autoImportReady(res?.pipeline)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      await loadPipeline()
    } finally {
      setBusy('')
    }
  }, [
    autoImportReady,
    campaignId,
    freeze,
    loadPipeline,
    pipeline?.fit_threshold,
    pipelineReady,
  ])

  // Auto-start once when campaign has no ready leads yet
  useEffect(() => {
    if (autoStartedRef.current) return
    if (freeze || statusLoading || pipelineLoading) return
    if (!pipelineReady || statusError || pipelineError) return
    if (busy) return
    const hasReady =
      readyCount > 0 ||
      importedCount > 0 ||
      (Array.isArray(pipeline?.prospecting_leads) &&
        pipeline.prospecting_leads.some((r) => r?.outreach_ready || r?.already_in_campaign))
    if (hasReady) {
      autoStartedRef.current = true
      return
    }
    if (pipeline?.run_state?.running) return
    autoStartedRef.current = true
    void runFullPipeline()
  }, [
    busy,
    freeze,
    importedCount,
    pipeline,
    pipelineError,
    pipelineLoading,
    pipelineReady,
    readyCount,
    runFullPipeline,
    statusError,
    statusLoading,
  ])

  const icpSummary = (
    campaign?.outreach_mode === 'b2c'
      ? [campaign?.target_interests, campaign?.target_country, campaign?.target_role]
      : [campaign?.target_industry, campaign?.target_country, campaign?.target_role]
  )
    .filter(Boolean)
    .join(' · ')

  const isRunning = Boolean(busy) || pipeline?.run_state?.running === true
  const panelErr = pipeline?.last_error || error
  const stageLabel = busy === 'import'
    ? 'Importando a la campaña…'
    : busy === 'full'
      ? `Buscando prospectos… ${elapsedSec}s`
      : STAGE_LABELS[currentStage] || currentStage

  return (
    <section className="rounded-xl border border-zinc-100/90 bg-white p-4 shadow-sm ring-1 ring-zinc-900/5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-900">Búsqueda de prospectos</h2>
          <p className="mt-0.5 max-w-xl text-xs leading-relaxed text-zinc-600">
            {isB2c
              ? 'Automático B2C: busca personas según región + quién/keywords (Prospeo) e importa a esta campaña.'
              : 'Automático: busca empresas del ICP, encuentra contactos y los importa a esta campaña.'}
          </p>
          {icpSummary ? (
            <p className="mt-1.5 text-[11px] font-medium text-zinc-800">ICP: {icpSummary}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-[10px] font-semibold text-zinc-700">
            {readyCount} listos · {importedCount} en campaña
          </span>
        </div>
      </div>

      {(statusLoading || pipelineLoading) && !statusError && !pipelineError ? (
        <p className="mt-3 text-xs text-zinc-500">Preparando búsqueda…</p>
      ) : null}

      {statusError || pipelineError ? (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-900">
          <p className="font-semibold">No se pudo cargar el motor de búsqueda</p>
          {statusError ? <p className="mt-1">{statusError}</p> : null}
          {pipelineError ? <p className="mt-1">{pipelineError}</p> : null}
          <button
            type="button"
            className="mt-2 rounded-md bg-red-100 px-3 py-1.5 text-[11px] font-semibold text-red-950 hover:bg-red-200"
            disabled={statusLoading || pipelineLoading}
            onClick={() => void Promise.all([loadStatus(), loadPipeline()])}
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {!pipelineReady && !statusLoading ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
          {isB2c
            ? 'B2C requiere Prospeo configurado en el servidor (PROSPEO_API_KEY).'
            : 'Falta configurar Web Search o Prospeo en el servidor (API keys).'}
        </div>
      ) : null}

      {prospeoBlocked ? (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900">
          {prospeoHealth?.banner_message || 'Prospeo sin créditos o limitado.'}
        </div>
      ) : null}

      {isRunning ? (
        <div className="mt-3 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2.5">
          <div className="flex items-center gap-2">
            <span className="size-2 animate-pulse rounded-full bg-nx-brand" />
            <p className="text-xs font-semibold text-zinc-900">{stageLabel}</p>
          </div>
          <p className="mt-1 text-[11px] text-zinc-600">
            No hace falta hacer nada: al terminar se importan solos los contactos listos.
          </p>
        </div>
      ) : null}

      {panelErr && !isRunning ? (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          <p>{panelErr}</p>
          <button
            type="button"
            className="mt-2 rounded-md bg-red-100 px-2.5 py-1 text-[11px] font-semibold text-red-900 hover:bg-red-200"
            disabled={freeze || !pipelineReady}
            onClick={() => void runFullPipeline()}
          >
            Reintentar búsqueda
          </button>
        </div>
      ) : null}

      {importMsg ? (
        <p className="mt-3 text-xs font-medium text-emerald-800">{importMsg}</p>
      ) : null}

      {!isRunning && readyCount === 0 && importedCount === 0 && pipelineReady && !pipelineLoading ? (
        <p className="mt-4 text-center text-xs text-zinc-500">
          Todavía no hay prospectos. La búsqueda arranca sola según el ICP de la campaña.
        </p>
      ) : null}

      {(people.length > 0 || readyCount > 0 || importedCount > 0) && !isRunning ? (
        <ProspectingLeadsTable
          rows={
            Array.isArray(pipeline?.prospecting_leads) && pipeline.prospecting_leads.length > 0
              ? pipeline.prospecting_leads
              : people.map((p) => ({
                  external_id: p.external_id,
                  person_name: p.name,
                  company_name: p.company_name,
                  role: p.role,
                  email: p.email,
                  linkedin_url: p.linkedin_url,
                  phone: p.phone,
                  whatsapp_number: p.whatsapp_number || p.whatsapp,
                  phone_source: p.phone_source,
                  outreach_ready: isOutreachReady(p),
                  linkedin_valid: hasRealLinkedInUrl(p.linkedin_url),
                  missing_fields: [],
                }))
          }
          phoneInfo={showOpsDebug ? pipeline?.prospeo_phone_info : null}
          selectedId={outreachSelectedId}
          onSelectRow={setOutreachSelectedId}
        />
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

      {showOpsDebug ? (
        <details className="mt-4 rounded-lg border border-dashed border-zinc-300 bg-zinc-50/50 p-3">
          <summary className="cursor-pointer text-xs font-semibold text-zinc-700">
            Detalle técnico (ops)
          </summary>
          <div className="mt-3 space-y-3">
            <MvpCompanyDomainsTable
              companies={companyCandidates}
              metrics={pipeline?.domain_resolution_metrics}
            />
            <MvpContactsTable
              rows={pipeline?.company_contacts || []}
              metrics={pipeline?.mvp_contact_metrics}
              selectedId={outreachSelectedId}
              onSelectContact={setOutreachSelectedId}
            />
            <ProspeoSearchDebugPanel rows={pipeline?.prospeo_search_debug || []} />
            <ProspeoContactDebugPanel rows={pipeline?.prospeo_contact_debug || []} />
            {Array.isArray(pipeline?.stage_logs) && pipeline.stage_logs.length > 0 ? (
              <ul className="max-h-40 space-y-1 overflow-y-auto text-[10px] text-zinc-600">
                {[...pipeline.stage_logs].reverse().slice(0, 20).map((log, idx) => (
                  <li key={`${log.at}-${log.step}-${idx}`}>
                    [{log.event}] {log.step}: {log.message || '—'}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </details>
      ) : null}
    </section>
  )
}
