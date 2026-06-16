import { useCallback, useEffect, useMemo, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { CollapsibleSection } from '../ui/CollapsibleSection.jsx'
import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import { CalendarSyncDebugPanel } from './CalendarSyncDebugPanel.jsx'
import { LinkedInAssistQueue } from './LinkedInAssistQueue.jsx'
import { AlertBanner } from '../AlertBanner.jsx'
import {
  createGmailDraft,
  sendGmailMessage,
  syncGmailInbound,
  syncGoogleCalendar,
  fetchCampaignMeetings,
  fetchCampaignOutreach,
  fetchCampaignProspects,
  fetchLinkedInAssistQueue,
  fetchCompanyOutreachTasks,
  abandonLinkedInAssistedSession,
  beginLinkedInAssistedSession,
  markLinkedInAssistedSent,
  reactivateProspectSequence,
  runScheduledCampaignFollowups,
  startCampaignOutreach,
  stopCampaignOutreach,
} from '../../utils/api.js'
import {
  SEQUENCE_MILESTONES,
  channelLabel,
  milestoneCompletionCounts,
  prospectNextMilestoneSummary,
  sequenceGroupLabel,
} from '../../utils/sequenceUi.js'
import { formatLocalDateTime } from '../../utils/instantFormat.js'
import {
  copyTextToClipboard,
  hasRealLinkedInUrl,
  linkedInOpenUrl,
} from '../../utils/linkedinAssist.js'

const showOpsDebug = import.meta.env.VITE_SHOW_OPS_DEBUG === 'true'

function fmtDate(iso) {
  return formatLocalDateTime(iso)
}

function Metric({ label, value, hint }) {
  return (
    <div className="rounded-lg border border-[#e5e7eb] bg-[#f8fafc] px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-[#6b7280]">{label}</p>
      <p className="text-lg font-semibold text-[#111827] tabular-nums">{value ?? 0}</p>
      {hint ? <p className="text-[10px] text-[#9ca3af]">{hint}</p> : null}
    </div>
  )
}

function buildUnifiedFeed(campaign, outreach, prospects) {
  const items = []
  const activityLog = Array.isArray(campaign?.outreach_activity_log)
    ? campaign.outreach_activity_log
    : []
  const logKindLabels = {
    linkedin_suggested: 'LinkedIn sugerido',
    linkedin_prepared: 'Mensaje preparado (LinkedIn)',
    linkedin_opened: 'LinkedIn abierto',
    linkedin_open: 'LinkedIn abierto',
    linkedin_copy: 'Mensaje copiado (LinkedIn)',
    linkedin_sent: 'LinkedIn confirmado enviado',
    linkedin_pending: 'LinkedIn pendiente de envío',
  }
  for (const row of activityLog) {
    const k = row.kind || 'info'
    const prefix = logKindLabels[k]
    items.push({
      at: row.at,
      text: prefix ? `${prefix} · ${row.message}` : row.message,
      source: 'log',
      kind: k,
    })
  }
  const pmap = new Map(prospects.map((p) => [p.id, p]))
  const messages = Array.isArray(outreach?.last_messages) ? outreach.last_messages : []
  for (const m of messages) {
    const name = pmap.get(m.prospect_id)?.name || `Prospecto #${m.prospect_id}`
    if (m.direction === 'inbound') {
      const raw = (m.message || '').trim()
      const text = raw.includes('[Gmail · respuesta real]')
        ? `Respuesta por email (Gmail) · ${name}`
        : `Nexus detectó respuesta · ${name}`
      items.push({
        at: m.created_at,
        text,
        source: 'msg',
        kind: 'inbound',
      })
    } else if (m.sender_type === 'system') {
      const raw = (m.message || '').trim()
      let line
      if (raw.startsWith('[Borrador Gmail')) {
        line = `Borrador Gmail guardado (sin enviar) · ${name}`
      } else if (raw.startsWith('[Google Calendar')) {
        line = `Reunión detectada (Google Calendar) · ${name}`
      } else {
        line = raw.slice(0, 220) || `Nexus dejó una nota operativa · ${name}`
      }
      items.push({
        at: m.created_at,
        text: line,
        source: 'msg',
        kind: 'system',
      })
    } else if (m.sender_type === 'ai') {
      const ch = channelLabel(m.channel)
      items.push({
        at: m.created_at,
        text: `Nexus generó mensaje (${ch}) · ${name}`,
        source: 'msg',
        kind: 'outbound',
      })
    } else if (m.sender_type === 'user' && m.direction === 'outbound') {
      const raw = (m.message || '').trim()
      const text = raw.startsWith('[LinkedIn · enviado por SDR]')
        ? `Mensaje enviado por SDR en LinkedIn · ${name}`
        : `Mensaje saliente · ${name}`
      items.push({
        at: m.created_at,
        text,
        source: 'msg',
        kind: 'linkedin_sent',
      })
    }
  }
  items.sort((a, b) => new Date(b.at) - new Date(a.at))
  const seen = new Set()
  const deduped = []
  for (const it of items) {
    const k = `${String(it.at).slice(0, 19)}|${(it.text || '').slice(0, 100)}`
    if (seen.has(k)) {
      continue
    }
    seen.add(k)
    deduped.push(it)
  }
  return deduped.slice(0, 80)
}

const SEQUENCE_FILTER_OPTIONS = [
  { key: 'all', label: 'Todos' },
  { key: 'contactado', label: 'Contactados' },
  { key: 'encajonado', label: 'Encajonados' },
  { key: 'proximo_follow_up', label: 'Próximos follow-ups' },
  { key: 'follow_ups', label: 'Follow-up' },
  { key: 'postergado', label: 'Postergados' },
  { key: 'descanso', label: 'Descanso' },
  { key: 'reuniones', label: 'Reunión agendada' },
]

/**
 * Outreach: centro operativo — feed vivo, secuencia 21d, grupos, LinkedIn premium.
 */
export function CampaignOutreachSection({
  campaignId,
  companyId,
  campaign,
  prospects = [],
  preferredProspectId = null,
  freeze = false,
  onChanged,
}) {
  const [prospectRows, setProspectRows] = useState(null)

  const list = useMemo(() => {
    if (prospectRows !== null) {
      return Array.isArray(prospectRows) ? prospectRows : []
    }
    return Array.isArray(prospects) ? prospects : []
  }, [prospectRows, prospects])

  useEffect(() => {
    setProspectRows(null)
  }, [campaignId])

  const emailProspects = useMemo(
    () => list.filter((p) => (p.email || '').trim()),
    [list],
  )

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [outreach, setOutreach] = useState(null)
  const [busyAction, setBusyAction] = useState('')
  const [meetingsN, setMeetingsN] = useState(0)
  const [pendingTasks, setPendingTasks] = useState(0)
  const [followupsSent, setFollowupsSent] = useState(0)
  const [liModal, setLiModal] = useState({
    open: false,
    prospect: null,
    text: '',
    clipboardOk: false,
    sessionStarted: false,
  })
  const [liBusy, setLiBusy] = useState(null)
  const [liQueue, setLiQueue] = useState({ tasks: [], total_pending: 0 })
  const [gmailDraftOk, setGmailDraftOk] = useState(null)
  const [gmailSendOk, setGmailSendOk] = useState(null)
  const [gmailSyncOk, setGmailSyncOk] = useState(null)
  const [calendarSyncOk, setCalendarSyncOk] = useState(null)
  const [calendarDebugExpanded, setCalendarDebugExpanded] = useState(showOpsDebug)
  const [gmailProspectId, setGmailProspectId] = useState(null)
  const [activationNote, setActivationNote] = useState(null)
  const [sequenceFilter, setSequenceFilter] = useState('all')
  const [sequenceSearch, setSequenceSearch] = useState('')

  const calendarDebugStorageKey = useMemo(() => {
    if (!companyId || !campaignId) {
      return null
    }
    return `nx-calendar-debug:v3:${companyId}:${campaignId}`
  }, [companyId, campaignId])

  const clearStoredCalendarDebug = useCallback(() => {
    setCalendarSyncOk(null)
    if (!calendarDebugStorageKey) {
      return
    }
    try {
      sessionStorage.removeItem(calendarDebugStorageKey)
    } catch {
      /* ignore */
    }
  }, [calendarDebugStorageKey])

  useEffect(() => {
    if (!calendarDebugStorageKey) {
      return
    }
    try {
      const raw = sessionStorage.getItem(calendarDebugStorageKey)
      if (!raw) {
        return
      }
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') {
        setCalendarSyncOk(parsed)
      }
    } catch {
      /* ignore */
    }
  }, [calendarDebugStorageKey])

  useEffect(() => {
    if (emailProspects.length === 0) {
      setGmailProspectId(null)
      return
    }
    const prefer = preferredProspectId
    const hit =
      prefer != null ? emailProspects.find((p) => Number(p.id) === Number(prefer)) : null
    setGmailProspectId(hit?.id ?? emailProspects[0]?.id ?? null)
  }, [emailProspects, preferredProspectId])

  const load = useCallback(async () => {
    if (!campaignId || freeze) {
      setOutreach(null)
      setProspectRows(null)
      setLoading(false)
      setMeetingsN(0)
      setPendingTasks(0)
      setFollowupsSent(0)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [data, mrows, tasks, prospectsRows, queue] = await Promise.all([
        fetchCampaignOutreach(campaignId),
        fetchCampaignMeetings(campaignId).catch(() => []),
        companyId
          ? fetchCompanyOutreachTasks(companyId, {
              status: 'pending',
              campaignId,
              limit: 40,
            }).catch(() => [])
          : Promise.resolve([]),
        fetchCampaignProspects(campaignId).catch(() => []),
        fetchLinkedInAssistQueue(campaignId).catch(() => ({ tasks: [], total_pending: 0 })),
      ])
      setOutreach(data)
      setMeetingsN(Array.isArray(mrows) ? mrows.length : 0)
      const po = Number(data?.pending_operational_tasks)
      setPendingTasks(Number.isFinite(po) ? po : (Array.isArray(tasks) ? tasks.length : 0))
      const rows = Array.isArray(prospectsRows) ? prospectsRows : []
      setProspectRows(rows)
      setFollowupsSent(rows.reduce((acc, p) => acc + (Number(p.followup_count) || 0), 0))
      setLiQueue(
        queue && typeof queue === 'object'
          ? { tasks: Array.isArray(queue.tasks) ? queue.tasks : [], total_pending: queue.total_pending ?? 0 }
          : { tasks: [], total_pending: 0 },
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setOutreach(null)
    } finally {
      setLoading(false)
    }
  }, [campaignId, companyId, freeze])

  useEffect(() => {
    void load()
  }, [load])

  const stats = outreach?.stats ?? {
    contacted: 0,
    responded: 0,
    interested: 0,
    not_interested: 0,
    failed: 0,
  }
  const realMode = outreach?.real_mode === true
  const running =
    outreach?.sequence?.is_running === true ||
    (campaign?.status === 'running' && campaign?.automation_paused !== true)
  const lastCycleAt = campaign?.autopilot_last_cycle_at
  const lastSummary = campaign?.autopilot_last_cycle_summary

  useEffect(() => {
    if (!running || freeze || !campaignId) {
      return undefined
    }
    const id = setInterval(() => {
      void load()
    }, 45_000)
    return () => clearInterval(id)
  }, [running, freeze, campaignId, load])

  function formatActivationResult(res) {
    const drafts = Number(res?.drafts) || 0
    const sent = Number(res?.sent) || 0
    const contacted = Number(res?.contacted_now) || 0
    const errs = Array.isArray(res?.error_messages) ? res.error_messages : []
    const parts = []
    if (res?.used_gmail) {
      if (drafts > 0) {
        parts.push(`${drafts} borrador${drafts === 1 ? '' : 'es'} en Gmail`)
      }
      if (sent > 0) {
        parts.push(`${sent} email${sent === 1 ? '' : 's'} enviado${sent === 1 ? '' : 's'}`)
      }
      if (contacted === 0 && drafts === 0 && sent === 0) {
        parts.push('motor activo; el scheduler seguirá con prospectos pendientes')
      }
    } else if (contacted > 0) {
      parts.push(`${contacted} primeros contactos (modo simulación en BD)`)
    } else if (res?.gmail_connected === false) {
      parts.push('conectá Gmail del vendedor para outreach real automático')
    } else {
      parts.push('motor activo; revisá prospectos con email sin contacto previo')
    }
    if (errs.length > 0) {
      parts.push(`avisos: ${errs[0]}`)
    }
    return parts.join(' · ')
  }

  async function handleStartCampaign() {
    setBusyAction('toggle')
    setError(null)
    setActivationNote(null)
    try {
      const res = await startCampaignOutreach(campaignId)
      setActivationNote(formatActivationResult(res))
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  async function handlePauseCampaign() {
    setBusyAction('toggle')
    setError(null)
    setActivationNote(null)
    try {
      await stopCampaignOutreach(campaignId)
      setActivationNote('Campaña pausada. Nexus no enviará ni hará follow-ups hasta que la reanudes.')
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  async function handleToggleCampaign() {
    if (running) {
      await handlePauseCampaign()
    } else {
      await handleStartCampaign()
    }
  }

  async function handleCreateGmailDraft() {
    setBusyAction('gmail')
    setError(null)
    setGmailDraftOk(null)
    if (!companyId) {
      setError('Falta company_id.')
      setBusyAction('')
      return
    }
    const sellerId = campaign?.seller_id
    if (!sellerId) {
      setError('La campaña no tiene vendedor asignado; no se puede usar su Gmail conectado.')
      setBusyAction('')
      return
    }
    const target = emailProspects.find((p) => Number(p.id) === Number(gmailProspectId))
    if (!target) {
      setError(
        'No hay prospecto con email en esta campaña. Agregá un email al prospecto o elegí uno en la lista.',
      )
      setBusyAction('')
      return
    }
    try {
      const res = await createGmailDraft({
        user_id: sellerId,
        company_id: companyId,
        campaign_id: campaignId,
        prospect_id: target.id,
      })
      setGmailDraftOk(res)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  async function handleSendGmailEmail() {
    if (
      !window.confirm(
        'Se enviará un correo REAL desde tu cuenta Gmail al prospecto seleccionado. ¿Confirmás el envío?',
      )
    ) {
      return
    }
    setBusyAction('gmail-send')
    setError(null)
    setGmailSendOk(null)
    if (!companyId) {
      setError('Falta company_id.')
      setBusyAction('')
      return
    }
    const sellerId = campaign?.seller_id
    if (!sellerId) {
      setError('La campaña no tiene vendedor asignado; no se puede usar su Gmail conectado.')
      setBusyAction('')
      return
    }
    const target = emailProspects.find((p) => Number(p.id) === Number(gmailProspectId))
    if (!target) {
      setError(
        'No hay prospecto con email en esta campaña. Agregá un email al prospecto o elegí uno en la lista.',
      )
      setBusyAction('')
      return
    }
    try {
      const res = await sendGmailMessage({
        user_id: sellerId,
        company_id: companyId,
        campaign_id: campaignId,
        prospect_id: target.id,
        confirm_send: true,
      })
      setGmailSendOk(res)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  async function handleSyncGmailInbound() {
    setBusyAction('gmail-sync')
    setError(null)
    setGmailSyncOk(null)
    if (!companyId) {
      setError('Falta company_id.')
      setBusyAction('')
      return
    }
    const sellerId = campaign?.seller_id
    if (!sellerId) {
      setError('La campaña no tiene vendedor asignado; no se puede leer su Gmail.')
      setBusyAction('')
      return
    }
    try {
      const res = await syncGmailInbound({
        user_id: sellerId,
        company_id: companyId,
        campaign_id: campaignId,
      })
      setGmailSyncOk(res)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  async function handleSyncGoogleCalendar() {
    setBusyAction('calendar-sync')
    setError(null)
    setCalendarSyncOk(null)
    if (!companyId) {
      setError('Falta company_id.')
      setBusyAction('')
      return
    }
    const sellerId = campaign?.seller_id
    if (!sellerId) {
      setError('La campaña no tiene vendedor asignado; no se puede leer su Google Calendar.')
      setBusyAction('')
      return
    }
    try {
      const res = await syncGoogleCalendar({
        user_id: sellerId,
        company_id: companyId,
        campaign_id: campaignId,
        include_debug: true,
        client_now_utc: new Date().toISOString(),
      })
      setCalendarSyncOk(res)
      try {
        if (calendarDebugStorageKey) {
          sessionStorage.setItem(calendarDebugStorageKey, JSON.stringify(res))
        }
      } catch {
        /* ignore */
      }
      setCalendarDebugExpanded(true)
      if (typeof console !== 'undefined' && console.info) {
        console.info('[Nexus] Calendar sync response', res)
      }
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  async function handleRunFollowupsWorker() {
    setBusyAction('followups')
    setError(null)
    try {
      await runScheduledCampaignFollowups(campaignId)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  function prospectFromTask(task) {
    if (!task) {
      return null
    }
    const hit = list.find((p) => p.id === task.prospect_id)
    if (hit) {
      return hit
    }
    return {
      id: task.prospect_id,
      name: task.prospect_name,
      company_name: task.company_name,
      linkedin_url: task.linkedin_url,
      linkedin_assisted_draft: task.message,
    }
  }

  async function handleAbrirLinkedIn(input) {
    const p = input?.prospect_id ? prospectFromTask(input) : input
    if (!p) {
      return
    }
    setLiBusy(p.id)
    setError(null)
    const profileUrl = linkedInOpenUrl(p.linkedin_url)
    if (!hasRealLinkedInUrl(p.linkedin_url)) {
      setError(
        `${p.name} no tiene un perfil LinkedIn real. Configurá linkedin.com/in/... válido.`,
      )
      setLiBusy(null)
      return
    }
    try {
      const res = await beginLinkedInAssistedSession(p.id)
      const text = (res?.message || p.linkedin_assisted_draft || input?.message || '').trim()
      const clipboardOk = await copyTextToClipboard(text)
      if (profileUrl) {
        window.open(profileUrl, '_blank', 'noopener,noreferrer')
      }
      setLiModal({
        open: true,
        prospect: p,
        text,
        clipboardOk,
        sessionStarted: true,
      })
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiBusy(null)
    }
  }

  function handleCloseLiModal() {
    setLiModal({ open: false, prospect: null, text: '', clipboardOk: false, sessionStarted: false })
  }

  async function handleAbandonLiModal() {
    const p = liModal.prospect
    setLiModal({ open: false, prospect: null, text: '', clipboardOk: false, sessionStarted: false })
    if (!p) {
      return
    }
    try {
      await abandonLinkedInAssistedSession(p.id)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleMarkLiSent(prospectOverride) {
    const raw = prospectOverride?.prospect_id ? prospectFromTask(prospectOverride) : prospectOverride
    const p = raw || liModal.prospect
    if (!p) {
      return
    }
    if (!hasRealLinkedInUrl(p.linkedin_url)) {
      setError('No se puede marcar enviado: el prospecto no tiene LinkedIn real.')
      return
    }
    setLiBusy(p.id)
    try {
      await markLinkedInAssistedSent(p.id)
      setLiModal({ open: false, prospect: null, text: '', clipboardOk: false, sessionStarted: false })
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiBusy(null)
    }
  }

  const liTasks = liQueue.tasks ?? []

  const unifiedFeed = useMemo(
    () => buildUnifiedFeed(campaign, outreach, list),
    [campaign, outreach, list],
  )

  const sequenceActiveList = useMemo(() => {
    return list.filter((p) => {
      const g = String(p.sequence_group || '').toLowerCase()
      return g !== 'reuniones' && g !== 'encajonado'
    })
  }, [list])

  const { counts: milestoneCounts } = useMemo(
    () => milestoneCompletionCounts(sequenceActiveList),
    [sequenceActiveList],
  )

  const filteredProspects = useMemo(() => {
    let rows = [...list]
    if (sequenceFilter !== 'all') {
      rows = rows.filter(
        (p) => String(p.sequence_group || 'contactado').toLowerCase() === sequenceFilter,
      )
    }
    const q = sequenceSearch.trim().toLowerCase()
    if (q) {
      rows = rows.filter(
        (p) =>
          (p.name || '').toLowerCase().includes(q) ||
          (p.company_name || '').toLowerCase().includes(q) ||
          (p.email || '').toLowerCase().includes(q),
      )
    }
    return rows.sort((a, b) => {
      const ta = a.sequence_started_at ? new Date(a.sequence_started_at).getTime() : 0
      const tb = b.sequence_started_at ? new Date(b.sequence_started_at).getTime() : 0
      return tb - ta
    })
  }, [list, sequenceFilter, sequenceSearch])

  const sequenceRows = useMemo(
    () =>
      filteredProspects.map((p) => ({
        p,
        summary: prospectNextMilestoneSummary(p, campaign),
      })),
    [filteredProspects, campaign],
  )

  const encajonados = list.filter((p) => (p.sequence_group || '') === 'encajonado')
  const postergados = list.filter((p) => (p.sequence_group || '') === 'postergado')
  const notifCount = liTasks.length + postergados.length + encajonados.length

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-[#111827]">Motor de campaña</h2>
            {running ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-2.5 py-0.5 text-[11px] font-semibold text-rose-900 ring-1 ring-rose-200/90">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400/70 opacity-60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-nx-brand" />
                </span>
                Nexus activo
              </span>
            ) : (
              <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-[11px] font-medium text-zinc-600 ring-1 ring-zinc-200/80">
                En pausa
              </span>
            )}
          </div>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[#6b7280]">
            <span className="font-medium text-zinc-700">Iniciar campaña</span> enciende el motor autónomo: primer
            contacto por Gmail, detección de respuestas, follow-ups y pipeline — sin pasos manuales.
          </p>
        </div>
        {running ? (
          <button
            type="button"
            disabled={freeze || loading || busyAction !== '' || !campaignId}
            className="shrink-0 rounded-xl border border-zinc-300 bg-white px-4 py-2 text-sm font-semibold text-zinc-800 shadow-sm hover:bg-zinc-50 disabled:opacity-40"
            onClick={() => void handleToggleCampaign()}
          >
            {busyAction === 'toggle' ? 'Pausando…' : 'Pausar campaña'}
          </button>
        ) : (
          <PremiumGradientButton
            disabled={freeze || loading || busyAction !== '' || !campaignId}
            onClick={() => void handleToggleCampaign()}
          >
            {busyAction === 'toggle' ? 'Iniciando…' : 'Iniciar campaña'}
          </PremiumGradientButton>
        )}
      </div>

      {activationNote ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
          {activationNote}
        </div>
      ) : null}

      {showOpsDebug ? (
      <div className="flex flex-col gap-3 rounded-lg border border-dashed border-zinc-300 bg-zinc-50/80 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
          Operaciones manuales (debug)
        </p>
        <div className="flex flex-wrap items-end gap-3">
          {emailProspects.length > 0 ? (
            <div className="flex min-w-[12rem] flex-col gap-1">
              <label htmlFor="nx-gmail-draft-prospect" className="text-[11px] font-medium text-zinc-600">
                Borrador para (email)
              </label>
              <select
                id="nx-gmail-draft-prospect"
                disabled={freeze || loading || busyAction !== ''}
                className="max-w-xs rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-900 shadow-sm disabled:opacity-50"
                value={gmailProspectId != null ? String(gmailProspectId) : ''}
                onChange={(e) => setGmailProspectId(Number(e.target.value))}
              >
                {emailProspects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {(p.email || '').trim()}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={
            freeze ||
            loading ||
            busyAction !== '' ||
            !campaignId ||
            !companyId ||
            !campaign?.seller_id ||
            emailProspects.length === 0 ||
            gmailProspectId == null
          }
          className="rounded-lg border border-zinc-300 bg-zinc-50 px-3 py-2 text-xs font-semibold text-zinc-800 hover:bg-zinc-100 disabled:opacity-40"
          onClick={() => void handleCreateGmailDraft()}
          title="Genera asunto y cuerpo con IA (campaña, ICP, prospecto, historial, Educación IA) y crea un borrador en Gmail del vendedor. No envía."
        >
          {busyAction === 'gmail' ? 'Creando borrador…' : 'Crear borrador real'}
        </button>
        <button
          type="button"
          disabled={
            freeze ||
            loading ||
            busyAction !== '' ||
            !campaignId ||
            !companyId ||
            !campaign?.seller_id ||
            emailProspects.length === 0 ||
            gmailProspectId == null
          }
          className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-950 hover:bg-rose-100 disabled:opacity-40"
          onClick={() => void handleSendGmailEmail()}
          title="Genera con IA (como borrador) y envía por Gmail API. Pedís confirmación en el navegador antes de enviar."
        >
          {busyAction === 'gmail-send' ? 'Enviando…' : 'Enviar email real'}
        </button>
        <button
          type="button"
          disabled={
            freeze ||
            loading ||
            busyAction !== '' ||
            !campaignId ||
            !companyId ||
            !campaign?.seller_id
          }
          className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs font-semibold text-sky-900 hover:bg-sky-100 disabled:opacity-40"
          onClick={() => void handleSyncGmailInbound()}
          title="Lee respuestas reales en Gmail de los prospectos de esta campaña y las guarda en el historial (Postergados, IA, tareas)."
        >
          {busyAction === 'gmail-sync' ? 'Sincronizando…' : 'Sincronizar Gmail (inbound)'}
        </button>
        <button
          type="button"
          disabled={
            freeze ||
            loading ||
            busyAction !== '' ||
            !campaignId ||
            !companyId ||
            !campaign?.seller_id
          }
          className="rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-semibold text-violet-900 hover:bg-violet-100 disabled:opacity-40"
          onClick={() => void handleSyncGoogleCalendar()}
          title="Lee tu calendario principal de Google: si un evento tiene como invitado el email de un prospecto de esta campaña, crea/actualiza la reunión en Nexus y marca Agendado."
        >
          {busyAction === 'calendar-sync' ? 'Sincronizando…' : 'Sincronizar Calendar'}
        </button>
      </div>
      </div>
      ) : null}

      {showOpsDebug && campaignId && companyId ? (
        <section
          id="nx-debug-calendar"
          className="mt-4 w-full scroll-mt-4 rounded-xl border-2 border-amber-500 bg-amber-50 p-4 shadow-md ring-1 ring-amber-600/20"
          aria-label="Debug Calendar"
        >
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-amber-400/60 pb-3">
            <div className="min-w-[12rem] max-w-2xl">
              <h2 className="text-base font-bold tracking-tight text-amber-950">Debug Calendar</h2>
              <p className="mt-1 text-xs leading-relaxed text-amber-900">
                Acá ves el último resultado de <strong>Sincronizar Calendar</strong>: eventos leídos, invitados
                (attendees), si hubo match con un prospecto, <code className="rounded bg-amber-200/50 px-1">skip_reason</code>,
                reunión creada/actualizada y si el pipeline se actualizó. Se guarda en esta pestaña al recargar.
              </p>
              {!campaign?.seller_id ? (
                <p className="mt-2 text-xs font-medium text-rose-800">
                  Esta campaña no tiene vendedor asignado: el botón Sincronizar Calendar no va a funcionar hasta entonces.
                </p>
              ) : null}
              {calendarSyncOk ? (
                <p className="mt-2 text-xs font-semibold text-amber-950">
                  Resumen: eventos {calendarSyncOk.events_seen ?? 0} · GET ok {calendarSyncOk.events_enriched ?? 0}{' '}
                  · match {calendarSyncOk.matched ?? 0} · meetings creados {calendarSyncOk.created ?? 0} ·
                  pipeline {calendarSyncOk.pipeline_updated ?? 0}
                </p>
              ) : (
                <p className="mt-2 text-xs text-amber-900">
                  <strong>Todavía no hay datos.</strong> Pulsá el botón violeta <strong>Sincronizar Calendar</strong>{' '}
                  arriba.
                </p>
              )}
            </div>
            <div className="flex flex-shrink-0 flex-wrap items-center gap-2">
              <button
                type="button"
                className="rounded-lg border-2 border-amber-800 bg-amber-100 px-3 py-2 text-xs font-bold text-amber-950 shadow-sm hover:bg-amber-200"
                onClick={() => setCalendarDebugExpanded((v) => !v)}
              >
                {calendarDebugExpanded ? 'Ocultar Debug Calendar' : 'Ver Debug Calendar'}
              </button>
              {calendarSyncOk ? (
                <button
                  type="button"
                  className="rounded-lg border border-amber-700 bg-white px-3 py-2 text-xs font-semibold text-amber-950 hover:bg-amber-50"
                  onClick={() => clearStoredCalendarDebug()}
                >
                  Limpiar
                </button>
              ) : null}
            </div>
          </div>
          {calendarDebugExpanded ? (
            calendarSyncOk ? (
              <CalendarSyncDebugPanel
                data={calendarSyncOk}
                embedded
                onClose={() => setCalendarDebugExpanded(false)}
              />
            ) : (
              <div className="mt-4 rounded-lg border border-dashed border-amber-500 bg-white/70 p-4 text-sm text-amber-950">
                <p className="font-semibold">Esperando un sync de calendario…</p>
                <p className="mt-1 text-xs text-amber-900">
                  Cuando la sincronización termine bien, vas a ver acá la lista de eventos, attendees y matches.
                  Si falla el API, el error aparece en la banda roja de arriba.
                </p>
              </div>
            )
          ) : null}
        </section>
      ) : null}

      {showOpsDebug && gmailDraftOk ? (
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
          <div>
            <p className="font-semibold">Borrador creado en Gmail</p>
            {gmailDraftOk.subject ? (
              <p className="mt-1 text-xs text-emerald-900/90">
                Asunto: <span className="font-medium">{gmailDraftOk.subject}</span>
              </p>
            ) : null}
            <p className="mt-1 text-xs text-emerald-900/90">
              draft_id: <span className="font-mono">{gmailDraftOk.draft_id}</span>
              {gmailDraftOk.message_id ? (
                <>
                  {' '}
                  · message_id: <span className="font-mono">{gmailDraftOk.message_id}</span>
                </>
              ) : null}
            </p>
            {gmailDraftOk.gmail_web_link ? (
              <a
                href={gmailDraftOk.gmail_web_link}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block text-xs font-semibold text-emerald-900 underline"
              >
                Abrir en Gmail
              </a>
            ) : null}
          </div>
          <button
            type="button"
            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-emerald-900 hover:bg-emerald-100"
            onClick={() => setGmailDraftOk(null)}
          >
            Cerrar
          </button>
        </div>
      ) : null}

      {showOpsDebug && gmailSendOk ? (
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-950">
          <div>
            <p className="font-semibold">Email enviado por Gmail</p>
            {gmailSendOk.subject ? (
              <p className="mt-1 text-xs text-rose-900/90">
                Asunto: <span className="font-medium">{gmailSendOk.subject}</span>
              </p>
            ) : null}
            <p className="mt-1 text-xs text-rose-900/90">
              message_id:{' '}
              <span className="font-mono">{gmailSendOk.gmail_message_id || '—'}</span>
              {gmailSendOk.thread_id ? (
                <>
                  {' '}
                  · thread: <span className="font-mono">{gmailSendOk.thread_id}</span>
                </>
              ) : null}
              {gmailSendOk.outreach_message_id ? (
                <>
                  {' '}
                  · timeline id: <span className="font-mono">{gmailSendOk.outreach_message_id}</span>
                </>
              ) : null}
            </p>
            {gmailSendOk.gmail_web_link ? (
              <a
                href={gmailSendOk.gmail_web_link}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block text-xs font-semibold text-rose-900 underline"
              >
                Abrir en Gmail
              </a>
            ) : null}
          </div>
          <button
            type="button"
            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-rose-900 hover:bg-rose-100"
            onClick={() => setGmailSendOk(null)}
          >
            Cerrar
          </button>
        </div>
      ) : null}

      {showOpsDebug && gmailSyncOk ? (
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950">
          <div>
            <p className="font-semibold">Gmail sincronizado</p>
            <p className="mt-1 text-xs text-sky-900/90">
              Importados: <span className="font-medium">{gmailSyncOk.imported ?? 0}</span>
              {' · '}
              Sin hilo encontrado: <span className="font-medium">{gmailSyncOk.skipped_no_thread ?? 0}</span>
              {' · '}
              Hilos revisados: <span className="font-medium">{gmailSyncOk.threads_examined ?? 0}</span>
            </p>
            {Array.isArray(gmailSyncOk.errors) && gmailSyncOk.errors.length > 0 ? (
              <ul className="mt-2 list-disc pl-4 text-xs text-sky-900/80">
                {gmailSyncOk.errors.slice(0, 6).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            ) : null}
          </div>
          <button
            type="button"
            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-sky-900 hover:bg-sky-100"
            onClick={() => setGmailSyncOk(null)}
          >
            Cerrar
          </button>
        </div>
      ) : null}

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {freeze ? (
        <p className="text-xs text-amber-800">Seleccioná la empresa correcta en el header.</p>
      ) : null}

      <CollapsibleSection
        title="Centro de outreach"
        subtitle="Actividad en vivo, secuencia 21d y follow-ups"
        defaultOpen
        badge={running ? 'Activo' : 'Pausado'}
      >
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
        <Metric
          label="Estado outreach"
          value={running ? 'Activo' : 'Pausado'}
          hint={`Secuencia paso ${outreach?.sequence?.current_step ?? 0}`}
        />
        <Metric label="Último proceso" value={fmtDate(lastCycleAt)} hint="Último ciclo automático" />
        <Metric
          label="Contactados"
          value={stats.contacted}
          hint={
            realMode
              ? 'Sólo cuentan envíos Gmail reales registrados (mensaje saliente con ID de Gmail).'
              : 'Prospectos con al menos un mensaje saliente (IA o sistema, p. ej. borrador Gmail) en esta campaña'
          }
        />
        <Metric
          label="Respuestas"
          value={stats.responded}
          hint={
            realMode
              ? 'Sólo réplicas importadas desde Gmail (inbound con gmail_message_id).'
              : 'Prospectos con al menos un inbound registrado (p. ej. Gmail sync o chat)'
          }
        />
        <Metric label="Follow-ups enviados (suma)" value={followupsSent} />
        <Metric
          label="Follow-ups pendientes"
          value={pendingTasks}
          hint="Tareas pendientes: seguimiento, postergación, revisar inbound, etc."
        />
        <Metric
          label="Citas en calendario"
          value={meetingsN}
          hint="Solo reuniones con fecha registrada en Nexus."
        />
      </div>

      {lastSummary && typeof lastSummary === 'object' && Object.keys(lastSummary).length > 0 ? (
        <p className="text-[11px] text-[#6b7280]">
          Última ejecución:{' '}
          {typeof lastSummary.processed === 'number' ? `${lastSummary.processed} contactos` : null}
          {lastSummary.messages_generated != null ? ` · mensajes ${lastSummary.messages_generated}` : ''}
          {lastSummary.responses_simulated != null
            ? ` · respuestas ${lastSummary.responses_simulated}`
            : ''}
        </p>
      ) : null}

      {/* A — Actividad Nexus (live) */}
      <div className="overflow-hidden rounded-xl border border-zinc-200/90 bg-gradient-to-b from-zinc-50 to-white">
        <div className="flex items-center justify-between border-b border-zinc-800/20 bg-gradient-to-r from-zinc-900 via-zinc-900 to-zinc-800 px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold tracking-tight text-white">Actividad de Nexus</h3>
            <p className="text-[11px] text-zinc-400">Log de campaña y últimos mensajes en vivo</p>
          </div>
          <span className="rounded-md bg-white/10 px-2 py-1 text-[10px] font-medium text-zinc-200 ring-1 ring-white/10">
            {unifiedFeed.length} eventos
          </span>
        </div>
        <div className="max-h-[min(22rem,50vh)] overflow-y-auto px-2 py-3 sm:px-4">
          {unifiedFeed.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-slate-500">
              Cuando Nexus ejecute ciclos o lleguen respuestas, vas a ver cada movimiento acá con fecha y hora.
            </p>
          ) : (
            <ul className="relative space-y-0 border-l-2 border-rose-200/80 pl-4">
              {unifiedFeed.map((row, i) => (
                <li key={`${row.at}-${i}`} className="relative pb-4 pl-1 last:pb-1">
                  <span className="absolute -left-[9px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-nx-brand shadow-sm shadow-rose-900/20" />
                  <p className="text-[11px] font-medium text-slate-400">{fmtDate(row.at)}</p>
                  <p className="mt-0.5 text-sm leading-snug text-slate-800">{row.text}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Secuencia en curso */}
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-[#111827]">Secuencia en curso (21 días + día 42)</h3>
            <p className="mt-0.5 text-xs text-[#6b7280]">
              {filteredProspects.length} prospectos · follow-ups pendientes: {pendingTasks}
            </p>
          </div>
          <button
            type="button"
            disabled={freeze || loading || busyAction !== '' || !campaignId}
            className="rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-xs font-semibold text-zinc-800 hover:bg-zinc-50 disabled:opacity-40"
            onClick={() => void handleRunFollowupsWorker()}
          >
            {busyAction === 'followups' ? 'Procesando…' : 'Procesar follow-ups'}
          </button>
        </div>
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <input
            type="search"
            placeholder="Buscar prospecto…"
            value={sequenceSearch}
            onChange={(e) => setSequenceSearch(e.target.value)}
            className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm shadow-sm sm:max-w-xs"
          />
          <div className="flex flex-wrap gap-1.5">
            {SEQUENCE_FILTER_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                onClick={() => setSequenceFilter(opt.key)}
                className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 transition ${
                  sequenceFilter === opt.key
                    ? 'bg-zinc-900 text-white ring-zinc-900'
                    : 'bg-white text-zinc-600 ring-zinc-200 hover:bg-zinc-50'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-1 sm:gap-0">
          {SEQUENCE_MILESTONES.map((d, idx) => (
            <div key={d} className="flex items-center">
              <div
                className={`flex min-w-[3.25rem] flex-col items-center rounded-lg border px-2 py-2 text-center sm:min-w-[3.75rem] ${
                  milestoneCounts[d] > 0
                    ? 'border-rose-200/90 bg-rose-50/80 shadow-sm shadow-rose-950/5'
                    : 'border-zinc-100 bg-zinc-50/80'
                }`}
              >
                <span className="text-[10px] font-semibold uppercase text-slate-500">Día</span>
                <span className="text-sm font-bold tabular-nums text-slate-900">{d}</span>
                <span className="text-[10px] text-slate-500">{milestoneCounts[d]} ok</span>
              </div>
              {idx < SEQUENCE_MILESTONES.length - 1 ? (
                <span className="hidden px-0.5 text-slate-300 sm:inline" aria-hidden>
                  →
                </span>
              ) : null}
            </div>
          ))}
        </div>

        <div className="mt-5 overflow-x-auto rounded-lg border border-slate-100">
          <table className="min-w-[640px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">Prospecto</th>
                <th className="px-3 py-2">Día actual</th>
                <th className="px-3 py-2">Próximo paso</th>
                <th className="px-3 py-2">Canal siguiente</th>
                <th className="px-3 py-2">Estado</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sequenceRows.map(({ p, summary }) => (
                <tr key={p.id} className="hover:bg-slate-50/80">
                  <td className="px-3 py-2">
                    <span className="font-medium text-slate-900">{p.name}</span>
                    <span className="block text-[11px] text-slate-500">{p.company_name || '—'}</span>
                  </td>
                  <td className="px-3 py-2 tabular-nums text-slate-700">
                    {summary.currentDay > 0 ? `Día ${summary.currentDay}` : '—'}
                  </td>
                  <td className="max-w-[14rem] px-3 py-2 text-slate-700">
                    <span className="line-clamp-2" title={summary.line}>
                      {summary.line}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-800">{summary.channelLabel}</td>
                  <td className="px-3 py-2 text-slate-600">{sequenceGroupLabel(p.sequence_group)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sequenceRows.length === 0 ? (
            <p className="border-t border-slate-100 px-3 py-6 text-center text-xs text-slate-500">
              No hay prospectos con este filtro.
            </p>
          ) : null}
        </div>
      </div>

      </CollapsibleSection>

      <CollapsibleSection
        title="Notificaciones"
        subtitle="LinkedIn pendiente y acciones manuales del SDR"
        accent="notifications"
        badge={notifCount > 0 ? notifCount : null}
        defaultOpen={notifCount > 0}
      >
      {postergados.length ? (
        <div className="rounded-lg border border-sky-200 bg-sky-50/70 p-3">
          <h3 className="text-xs font-semibold text-sky-950">Postergados</h3>
          <p className="mt-0.5 text-[11px] text-sky-900/85">
            Interés posible a futuro: el prospecto pidió espacio. Nexus reactiva solo en la fecha programada; podés
            adelantar con el botón.
          </p>
          <ul className="mt-2 space-y-1 text-xs text-sky-950">
            {postergados.slice(0, 20).map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {p.name} · {p.company_name}
                  {p.defer_resume_at ? (
                    <span className="ml-1 text-[10px] text-sky-800/90">
                      · re-contacto ~ {fmtDate(p.defer_resume_at)}
                    </span>
                  ) : null}
                </span>
                <button
                  type="button"
                  disabled={freeze || liBusy === p.id}
                  className="rounded border border-sky-300 bg-white px-2 py-0.5 text-[11px] font-semibold hover:bg-sky-100 disabled:opacity-40"
                  onClick={async () => {
                    setLiBusy(p.id)
                    try {
                      await reactivateProspectSequence(p.id)
                      await load()
                      onChanged?.()
                    } catch (e) {
                      setError(e instanceof Error ? e.message : String(e))
                    } finally {
                      setLiBusy(null)
                    }
                  }}
                >
                  Reactivar ahora
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {encajonados.length ? (
        <div className="rounded-lg border border-amber-100 bg-amber-50/60 p-3">
          <h3 className="text-xs font-semibold text-amber-950">Encajonados</h3>
          <p className="mt-0.5 text-[11px] text-amber-900/80">
            Reactivá manualmente para volver a la cola de seguimiento.
          </p>
          <ul className="mt-2 space-y-1 text-xs text-amber-950">
            {encajonados.slice(0, 20).map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {p.name} · {p.company_name}
                </span>
                <button
                  type="button"
                  disabled={freeze || liBusy === p.id}
                  className="rounded border border-amber-300 bg-white px-2 py-0.5 text-[11px] font-semibold hover:bg-amber-100 disabled:opacity-40"
                  onClick={async () => {
                    setLiBusy(p.id)
                    try {
                      await reactivateProspectSequence(p.id)
                      await load()
                      onChanged?.()
                    } catch (e) {
                      setError(e instanceof Error ? e.message : String(e))
                    } finally {
                      setLiBusy(null)
                    }
                  }}
                >
                  Reactivar / Follow-up
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <LinkedInAssistQueue
        tasks={liTasks}
        freeze={freeze}
        busyProspectId={liBusy}
        onOpenLinkedIn={(task) => void handleAbrirLinkedIn(task)}
        onMarkSent={(task) => void handleMarkLiSent(task)}
      />

      {!notifCount ? (
        <p className="rounded-lg border border-dashed border-zinc-200 bg-zinc-50/60 px-4 py-6 text-center text-sm text-zinc-500">
          Sin acciones pendientes. LinkedIn, postergados y encajonados aparecen acá cuando requieran tu intervención.
        </p>
      ) : null}

      </CollapsibleSection>

      {liModal.open ? (
        <Modal
          title={`Mensaje · ${liModal.prospect?.name ?? ''}`}
          onClose={handleCloseLiModal}
          footer={
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded-lg border border-[#e5e7eb] px-3 py-1.5 text-xs"
                onClick={() => void handleAbandonLiModal()}
              >
                Cerrar sin enviar
              </button>
              <button
                type="button"
                className="rounded-lg border border-zinc-200 px-3 py-1.5 text-xs text-zinc-600"
                onClick={handleCloseLiModal}
              >
                Seguir después
              </button>
              <PremiumGradientButton
                className="px-4 py-2 text-xs"
                disabled={liBusy === liModal.prospect?.id}
                onClick={() => void handleMarkLiSent()}
              >
                Marcar como enviado (manual)
              </PremiumGradientButton>
            </div>
          }
        >
          <ol className="mb-3 list-decimal space-y-1 pl-4 text-xs text-zinc-600">
            <li>Revisá el mensaje en LinkedIn (ya debería estar en el portapapeles).</li>
            <li>Pegá en el chat y enviá manualmente.</li>
            <li>Solo entonces usá «Marcar como enviado (manual)» — abrir LinkedIn no cuenta como enviado.</li>
          </ol>
          {liModal.clipboardOk === false ? (
            <p className="mb-2 text-xs text-amber-800">
              No pudimos copiar al portapapeles: copiá el texto manualmente desde el cuadro.
            </p>
          ) : null}
          <textarea
            readOnly
            className="mt-1 w-full min-h-[10rem] rounded-lg border border-slate-200 bg-slate-50 p-2 text-sm"
            value={liModal.text}
          />
        </Modal>
      ) : null}
    </div>
  )
}
