import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { CollapsibleSection } from '../ui/CollapsibleSection.jsx'
import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import { CalendarSyncDebugPanel } from './CalendarSyncDebugPanel.jsx'
import { LinkedInAssistQueue } from './LinkedInAssistQueue.jsx'
import {
  LinkedInRespondieronModal,
  LinkedInRespondieronPanel,
} from './LinkedInRespondieronPanel.jsx'
import { WhatsAppAssistQueue } from './WhatsAppAssistQueue.jsx'
import { MailSentQueue } from './MailSentQueue.jsx'
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
  fetchWhatsAppAssistQueue,
  fetchMailQueue,
  fetchCompanyOutreachTasks,
  fetchGoogleIntegrationVerify,
  fetchLinkedInPendingConnectChecks,
  abandonLinkedInAssistedSession,
  beginLinkedInAssistedSession,
  markLinkedInAssistedSent,
  beginWhatsAppAssistedSession,
  markWhatsAppAssistedSent,
  markLinkedInConnectSent,
  reportLinkedInConnectionStatus,
  regenerateLinkedInAssistedReply,
  registerLinkedInInbound,
  pauseProspectSequence,
  reactivateProspectSequence,
  runScheduledCampaignFollowups,
  startCampaignOutreach,
  stopCampaignOutreach,
  continueCampaignWithoutChannel,
} from '../../utils/api.js'
import { clearProspectExtensionWatch } from '../../utils/clearProspectExtensionWatch.js'
import { useAuth } from '../../context/AuthContext.jsx'
import { isManagerOrGerente } from '../../data/navigation.js'
import {
  campaignMilestones,
  channelLabel,
  milestoneChannelChipClass,
  milestoneCompletionCounts,
  milestoneShortLabel,
  prospectNextMilestoneSummary,
  sequenceGroupLabel,
} from '../../utils/sequenceUi.js'
import { formatLocalDateTime } from '../../utils/instantFormat.js'
import {
  copyTextToClipboard,
  hasRealLinkedInUrl,
  linkedInMessageOpenUrl,
  linkedInOpenUrl,
} from '../../utils/linkedinAssist.js'
import {
  assistLinkedInOnExistingTabViaExtension,
  isNexusLinkedInExtensionInstalled,
  probeLinkedInConnectionViaExtension,
  probeLinkedInPendingNowViaExtension,
  resolveLinkedInComposeUrlViaExtension,
  syncLinkedInPendingToExtension,
} from '../../utils/linkedinAssistExtension.js'
import {
  LI_SAFE_NO_PROFILE_PROBE,
  clearLiContactarDone,
  clearLiRespondieronDismiss,
  handoffLiRespondieron,
  isLiRespondieronDismissed,
  markLiContactarDone,
  openLiSafeProfile,
} from '../../utils/linkedinLiSafe.js'
import {
  armWhatsAppOpenChatViaExtension,
  isNexusWhatsAppExtensionReady,
  syncWhatsAppPendingToExtension,
} from '../../utils/whatsappAssistExtension.js'
import { waWebSendUrl } from '../../utils/whatsappAssist.js'
import { notifyLinkedInQueueChanged } from '../../hooks/useLinkedInPending.js'
import { notifyWhatsAppQueueChanged } from '../../hooks/useWhatsAppPending.js'
import { notifyMeetingsChanged } from '../../hooks/useMeetingsPending.js'
import { ProspectQuotaBar } from '../campaigns/ProspectQuotaBar.jsx'
import { CampaignSetupChecklist } from '../campaigns/CampaignSetupChecklist.jsx'
import { ChannelEnrichCountdown } from '../campaigns/ChannelEnrichCountdown.jsx'
import { orderChannels } from '../../utils/campaignChannels.js'
import { hasUsableWhatsApp } from '../../utils/phoneUtils.js'
import { ProspectActivityBadge } from '../campaigns/ProspectActivityBadge.jsx'
import { showOpsDebug } from '../../utils/opsDebug.js'

function fmtDate(iso) {
  return formatLocalDateTime(iso)
}

function Metric({ label, value, hint }) {
  return (
    <div className="rounded-lg border border-nx-border bg-nx-card-muted px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-nx-muted">{label}</p>
      <p className="text-lg font-semibold text-nx-ink tabular-nums">{value ?? 0}</p>
      {hint ? <p className="text-[10px] text-nx-subtle">{hint}</p> : null}
    </div>
  )
}

function formatLinkedInProbeDiag(payload, fallbackName = '') {
  if (!payload || typeof payload !== 'object') return null
  if (payload.phase === 'opening' || payload.phase === 'reading') {
    return payload.summary || 'Leyendo grado en LinkedIn…'
  }
  if (payload.summary && !String(payload.summary).startsWith('Leyendo…')) {
    return String(payload.summary)
  }
  if (payload.summary && (payload.readOk || payload.ok || payload.error)) {
    // summary final (SÍ/NO) sí; "Leyendo…" solo si no hay error/ok aún
    if (!String(payload.summary).startsWith('Leyendo…') || payload.error || payload.readOk) {
      if (!String(payload.summary).startsWith('Leyendo…')) return String(payload.summary)
    }
  }
  const name = String(payload.prospectName || fallbackName || '').trim()
  const who = name ? ` · ${name}` : payload.prospectId ? ` · #${payload.prospectId}` : ''
  const via = payload.via ? ` · método: ${payload.via}` : ''
  const csrf =
    payload.csrf === true ? ' · CSRF ok' : payload.csrf === false ? ' · sin cookie JSESSIONID' : ''
  const d = Number(payload.degree)
  let label = payload.degreeLabel || null
  if (!label) {
    if (d === 1) label = '1º (contacto)'
    else if (d === 2) label = '2º (no contacto)'
    else if (d === 3) label = '3º (no contacto)'
    else if (String(payload.verdict || '').toLowerCase() === 'connected') label = '1º (contacto)'
    else if (String(payload.verdict || '').toLowerCase() === 'not_connected')
      label = '2º/3º (no contacto)'
  }
  const readOk =
    payload.readOk === true ||
    (payload.ok &&
      (label ||
        payload.verdict === 'connected' ||
        payload.verdict === 'not_connected' ||
        payload.reported))
  if (readOk && label) return `SÍ leyó${who}: ${label}${via}${csrf}`
  if (readOk && payload.verdict) return `SÍ leyó${who}: ${payload.verdict}${via}${csrf}`
  if (payload.summary && String(payload.summary).startsWith('Leyendo…') && !payload.error) {
    return String(payload.summary)
  }
  const err = String(payload.error || payload.reason || 'sin grado').trim()
  const attempts = Array.isArray(payload.attempts)
    ? ` · intentos: ${payload.attempts.map((a) => `${a.step}${a.ok ? '✓' : '✗'}`).join(',')}`
    : ''
  return `NO leyó${who}: ${err}${via}${csrf}${attempts}`
}

/**
 * Espera reporte de la extensión: connected | not_connected | null (timeout).
 */
function waitForLinkedInConnectionStatus(prospectId, timeoutMs = 18_000) {
  return new Promise((resolve) => {
    const started = Date.now()
    function onMsg(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_CONNECTION_REGISTERED') return
      const payload = data.payload || {}
      const pid = Number(payload.prospectId)
      if (!pid || pid !== Number(prospectId)) return
      const status = String(payload.connectionStatus || payload.connection_status || '').toLowerCase()
      cleanup()
      if (status === 'connected') resolve('connected')
      else if (status === 'not_connected' || status === 'invite_pending') resolve('not_connected')
      else resolve(status || 'connected')
    }
    function cleanup() {
      window.removeEventListener('message', onMsg)
      window.clearInterval(poll)
      window.clearTimeout(timer)
    }
    window.addEventListener('message', onMsg)
    const poll = window.setInterval(() => {
      if (Date.now() - started >= timeoutMs) {
        cleanup()
        resolve(null)
      }
    }, 400)
    const timer = window.setTimeout(() => {
      cleanup()
      resolve(null)
    }, timeoutMs)
  })
}

function softClip(text, maxLen = 180) {
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  if (t.length <= maxLen) return t
  const cut = t.slice(0, maxLen)
  const markers = ['. ', '? ', '! ', '; ', ', ', ' ']
  let best = -1
  for (const m of markers) {
    const idx = cut.lastIndexOf(m)
    if (idx > maxLen * 0.45 && idx > best) best = idx + (m.trim() ? m.trim().length : 0)
  }
  const base = best > 0 ? cut.slice(0, best).trim() : cut.trim()
  return `${base}…`
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
    inbound: 'Respuesta detectada',
  }
  const activityNoise = /Auto-respuesta deshabilitada \(NEXUS_INBOUND_AUTO_REPLY\)/i
  for (const row of activityLog) {
    const msg = String(row.message || '')
    if (activityNoise.test(msg)) {
      continue
    }
    const k = row.kind || 'info'
    const prefix = logKindLabels[k]
    items.push({
      at: row.at,
      text:
        prefix && !msg.toLowerCase().startsWith(prefix.toLowerCase().slice(0, 10))
          ? `${prefix} · ${msg}`
          : msg,
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
      let text
      let preview = ''
      if (raw.includes('[Gmail · respuesta real]')) {
        text = `Respuesta por email (Gmail) · ${name}`
        preview = raw.split('\n\n').slice(1).join('\n\n').trim()
      } else if (raw.includes('[LinkedIn · respuesta real]')) {
        text = `Respuesta por LinkedIn · ${name}`
        preview = raw.replace('[LinkedIn · respuesta real]', '').trim()
      } else {
        text = `Nexus detectó respuesta · ${name}`
        preview = raw
      }
      items.push({
        at: m.created_at,
        text: preview ? `${text} — «${softClip(preview, 180)}»` : text,
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
 * Outreach: centro operativo — feed vivo, secuencia 7 toques, grupos, LinkedIn premium.
 */
export function CampaignOutreachSection({
  campaignId,
  companyId,
  campaign,
  prospects = [],
  preferredProspectId = null,
  focusNotificaciones = false,
  freeze = false,
  onChanged,
}) {
  const { user } = useAuth()
  const showManagerTools = showOpsDebug
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
  /** Reuniones de campaña (para sacar Respondieron tras que sucedan). */
  const [campaignMeetings, setCampaignMeetings] = useState(/** @type {object[]} */ ([]))
  const [pendingTasks, setPendingTasks] = useState(0)
  const [followupsSent, setFollowupsSent] = useState(0)
  const [liModal, setLiModal] = useState({
    open: false,
    prospect: null,
    text: '',
    clipboardOk: false,
    sessionStarted: false,
    chatReady: false,
  })
  /** Prospecto en modal Respondieron (pegar inbound LinkedIn). */
  const [liRespondieron, setLiRespondieron] = useState(/** @type {object | null} */ (null))
  const [liBusy, setLiBusy] = useState(null)
  const [liRegenerating, setLiRegenerating] = useState(null)
  const [liQueue, setLiQueue] = useState({ tasks: [], total_pending: 0 })
  const [waQueue, setWaQueue] = useState({ tasks: [], total_pending: 0 })
  const [mailQueue, setMailQueue] = useState({ items: [], total: 0 })
  const [waBusy, setWaBusy] = useState(null)
  const [gmailDraftOk, setGmailDraftOk] = useState(null)
  const [gmailSendOk, setGmailSendOk] = useState(null)
  const [gmailSyncOk, setGmailSyncOk] = useState(null)
  const [calendarSyncOk, setCalendarSyncOk] = useState(null)
  const [calendarDebugExpanded, setCalendarDebugExpanded] = useState(showOpsDebug)
  const [gmailProspectId, setGmailProspectId] = useState(null)
  const [activationNote, setActivationNote] = useState(null)
  /** Countdown mientras el start espera Prospeo / enrich. */
  const [startEnrichWait, setStartEnrichWait] = useState(null)
  const [gmailConnected, setGmailConnected] = useState(null)
  const [sequenceFilter, setSequenceFilter] = useState('all')
  const [sequenceSearch, setSequenceSearch] = useState('')
  const [sequenceBlock, setSequenceBlock] = useState(null)
  const [progressNote, setProgressNote] = useState(null)
  const [skipChannelBusy, setSkipChannelBusy] = useState(false)
  const prevLiPendingRef = useRef(/** @type {number | null} */ (null))

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
      setCampaignMeetings([])
      setPendingTasks(0)
      setFollowupsSent(0)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [data, mrows, tasks, prospectsRows, queue, waQ, mailQ, googleVerify] = await Promise.all([
        fetchCampaignOutreach(campaignId),
        fetchCampaignMeetings(campaignId).catch(() => []),
        companyId
          ? fetchCompanyOutreachTasks(companyId, {
              status: 'pending',
              campaignId,
              limit: 40,
            }).catch(() => [])
          : Promise.resolve([]),
        fetchCampaignProspects(campaignId).catch(() => null),
        fetchLinkedInAssistQueue(campaignId).catch(() => ({ tasks: [], total_pending: 0 })),
        fetchWhatsAppAssistQueue(campaignId).catch(() => ({ tasks: [], total_pending: 0 })),
        fetchMailQueue(campaignId).catch(() => ({ items: [], total: 0 })),
        companyId && campaign?.seller_id
          ? fetchGoogleIntegrationVerify(companyId, campaign.seller_id, { deep: false }).catch(() => null)
          : Promise.resolve(null),
      ])
      const gmail = googleVerify?.gmail
      setGmailConnected(
        gmail == null
          ? null
          : Boolean(gmail.connected || gmail.effective_status === 'connected'),
      )
      setOutreach(data)
      setSequenceBlock(data?.sequence_block && typeof data.sequence_block === 'object' ? data.sequence_block : null)
      if (data?.progress_note) {
        const note = String(data.progress_note)
        // LI-SAFE: no mostrar ruido legacy de verify 1º/2º/3º en “Qué está pasando”.
        const liSafeNoise =
          LI_SAFE_NO_PROFILE_PROBE &&
          (/1º\/2º\/3º|verificando .*contacto|extension_not_responding/i.test(note) ||
            /Secuencia en espera\s*·\s*LinkedIn/i.test(note))
        setProgressNote(liSafeNoise ? null : note)
        if (liSafeNoise) setActivationNote(null)
      } else {
        setProgressNote(null)
      }
      setMeetingsN(Array.isArray(mrows) ? mrows.length : 0)
      setCampaignMeetings(Array.isArray(mrows) ? mrows : [])
      const po = Number(data?.pending_operational_tasks)
      setPendingTasks(Number.isFinite(po) ? po : (Array.isArray(tasks) ? tasks.length : 0))
      const rows = Array.isArray(prospectsRows) ? prospectsRows : null
      if (rows) {
        setProspectRows(rows)
        setFollowupsSent(rows.reduce((acc, p) => acc + (Number(p.followup_count) || 0), 0))
      }
      const liPending = Number(queue?.total_pending) || 0
      if (prevLiPendingRef.current !== null && liPending > prevLiPendingRef.current) {
        notifyLinkedInQueueChanged()
      }
      prevLiPendingRef.current = liPending

      setLiQueue(
        queue && typeof queue === 'object'
          ? {
              tasks: Array.isArray(queue.tasks) ? queue.tasks : [],
              total_pending: liPending,
              invites_remaining: queue.invites_remaining ?? null,
              invites_limit: queue.invites_limit ?? null,
              dms_remaining: queue.dms_remaining ?? null,
              dms_limit: queue.dms_limit ?? null,
              hidden_by_cap: queue.hidden_by_cap ?? 0,
              pending_verify: Number(queue.pending_verify) || 0,
            }
          : { tasks: [], total_pending: 0, pending_verify: 0 },
      )
      setWaQueue(
        waQ && typeof waQ === 'object'
          ? {
              tasks: Array.isArray(waQ.tasks) ? waQ.tasks : [],
              total_pending: Number(waQ.total_pending) || 0,
            }
          : { tasks: [], total_pending: 0 },
      )
      setMailQueue(
        mailQ && typeof mailQ === 'object'
          ? {
              items: Array.isArray(mailQ.items) ? mailQ.items : [],
              total: Number(mailQ.total) || (Array.isArray(mailQ.items) ? mailQ.items.length : 0),
            }
          : { items: [], total: 0 },
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setOutreach(null)
    } finally {
      setLoading(false)
    }
  }, [campaignId, companyId, campaign?.seller_id, freeze])

  useEffect(() => {
    void load()
  }, [load])

  // Tras insertar prospecto: refrescar cola. NO abrir LinkedIn hasta secuencia running.
  useEffect(() => {
    function onQueueChanged() {
      void load()
    }
    window.addEventListener('nx:linkedin-queue-changed', onQueueChanged)
    window.addEventListener('nx:whatsapp-queue-changed', onQueueChanged)
    return () => {
      window.removeEventListener('nx:linkedin-queue-changed', onQueueChanged)
      window.removeEventListener('nx:whatsapp-queue-changed', onQueueChanged)
    }
  }, [load])

  useEffect(() => {
    function onExtensionInbound(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_INBOUND_REGISTERED') return
      const payload = data.payload || {}
      notifyLinkedInQueueChanged({
        inbound: true,
        prospectId: payload.prospectId,
        replyDelayed: payload.replyDelayed,
      })
      void load()
    }
    function onWhatsAppInbound(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_WHATSAPP_INBOUND_REGISTERED') return
      const payload = data.payload || {}
      if (payload.calendarReconnectRequired) {
        window.alert(
          payload.operatorMessage ||
            'Google Calendar necesita reconexión. Andá a Configuración → Integraciones antes de confirmar la reunión.',
        )
      }
      notifyWhatsAppQueueChanged({
        inbound: true,
        prospectId: payload.prospectId,
        calendarReconnectRequired: Boolean(payload.calendarReconnectRequired),
      })
      void load()
    }
    async function onExtensionSent(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_SENT_REGISTERED') return
      const prospectId = Number(data.payload?.prospectId) || null
      if (prospectId) {
        setLiQueue((prev) => {
          const tasks = (prev.tasks || []).filter((t) => Number(t.prospect_id) !== prospectId)
          return { ...prev, tasks, total_pending: tasks.length }
        })
        try {
          await markLinkedInAssistedSent(prospectId)
        } catch {
          /* La extensión ya registró el envío; refrescamos igual abajo. */
        }
      }
      notifyLinkedInQueueChanged({ sent: true, prospectId })
      void load()
    }
    async function onWhatsAppSent(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_WHATSAPP_SENT_REGISTERED') return
      const prospectId = Number(data.payload?.prospectId) || null
      if (prospectId) {
        setWaQueue((prev) => {
          const tasks = (prev.tasks || []).filter((t) => Number(t.prospect_id) !== prospectId)
          return { ...prev, tasks, total_pending: tasks.length }
        })
        try {
          await markWhatsAppAssistedSent(prospectId)
        } catch {
          /* extensión ya marcó */
        }
      }
      notifyWhatsAppQueueChanged({ sent: true, prospectId })
      void load()
    }
    function onExtensionConnected(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_CONNECTION_REGISTERED') return
      const payload = data.payload || {}
      const status = String(payload.connectionStatus || payload.connection_status || '').toLowerCase()
      // connected → mensaje; not_connected / invite_pending → aparece Conectar.
      if (!status) return
      if (
        status !== 'connected' &&
        status !== 'not_connected' &&
        status !== 'invite_pending'
      ) {
        return
      }
      notifyLinkedInQueueChanged({
        connected: status === 'connected',
        prospectId: payload.prospectId,
        connectionStatus: status,
      })
      void load()
    }
    window.addEventListener('message', onExtensionInbound)
    window.addEventListener('message', onWhatsAppInbound)
    window.addEventListener('message', onExtensionSent)
    window.addEventListener('message', onWhatsAppSent)
    window.addEventListener('message', onExtensionConnected)
    return () => {
      window.removeEventListener('message', onExtensionInbound)
      window.removeEventListener('message', onWhatsAppInbound)
      window.removeEventListener('message', onExtensionSent)
      window.removeEventListener('message', onWhatsAppSent)
      window.removeEventListener('message', onExtensionConnected)
    }
  }, [load])

  // Mientras haya tareas LinkedIn, refrescar cola (sin probe).
  useEffect(() => {
    const waiting = (liQueue.tasks || []).some(
      (t) => t.action === 'connect' || t.action === 'message' || t.action === 'reply',
    )
    if (!waiting || freeze) return undefined
    const id = window.setInterval(() => {
      void load()
    }, 12_000)
    return () => window.clearInterval(id)
  }, [liQueue.tasks, freeze, load])

  // Sondeo checking: APAGADO en LI-SAFE (sin abrir perfiles en background).
  const lastPendingVerifyRef = useRef(0)
  const probeInFlightRef = useRef(false)
  const probeFailCountRef = useRef(0)
  const probeFinalLockRef = useRef(0)
  const [liProbeHint, setLiProbeHint] = useState(null)
  const [liProbeDiag, setLiProbeDiag] = useState(null)
  const [liVerifyTarget, setLiVerifyTarget] = useState(null)
  const [liContactarTick, setLiContactarTick] = useState(0)
  const [liRespondieronDismissTick, setLiRespondieronDismissTick] = useState(0)
  const running =
    outreach?.sequence?.is_running === true ||
    (campaign?.status === 'running' && campaign?.automation_paused !== true)
  const sequenceStartedByUser = outreach?.sequence?.is_running === true

  useEffect(() => {
    if (LI_SAFE_NO_PROFILE_PROBE) return undefined
    function onProbeDiag(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_PROBE_DIAG') return
      const payload = data.payload || data
      const text = formatLinkedInProbeDiag(payload)
      if (!text) return
      const isProgress =
        payload.phase === 'opening' ||
        payload.phase === 'reading' ||
        (String(text).startsWith('Leyendo') && !payload.error && !payload.verdict)
      // No pisar un SÍ/NO final con otro "Leyendo…"
      if (isProgress && Date.now() < Number(probeFinalLockRef.current || 0)) return
      if (isProgress && !probeInFlightRef.current && probeFailCountRef.current > 0) return
      const pending = Boolean(isProgress)
      const readOk = Boolean(
        !pending && (payload.readOk || (payload.ok && (payload.verdict || payload.degree))),
      )
      if (!pending && (readOk || payload.error || String(text).startsWith('NO leyó'))) {
        probeFinalLockRef.current = Date.now() + 90_000
      }
      setLiProbeDiag({ text, readOk, pending, at: Date.now(), raw: payload })
      setLiProbeHint(text)
      if (readOk) {
        probeFailCountRef.current = 0
        void load()
      }
    }
    window.addEventListener('message', onProbeDiag)
    return () => window.removeEventListener('message', onProbeDiag)
  }, [load])

  useEffect(() => {
    if (LI_SAFE_NO_PROFILE_PROBE) return undefined
    if (freeze || !companyId || !sequenceStartedByUser) {
      if (!sequenceStartedByUser) {
        lastPendingVerifyRef.current = 0
        probeFailCountRef.current = 0
        setLiProbeHint(null)
        setLiVerifyTarget(null)
      }
      return undefined
    }
    const pending = Number(liQueue.pending_verify || 0)
    if (pending <= 0) {
      lastPendingVerifyRef.current = 0
      probeFailCountRef.current = 0
      return undefined
    }
    if (probeFailCountRef.current >= 2) return undefined
    const isNewWave = pending > lastPendingVerifyRef.current
    lastPendingVerifyRef.current = pending

    let cancelled = false
    const applyFinal = (text, readOk, raw) => {
      probeFinalLockRef.current = Date.now() + 90_000
      setLiProbeHint(text)
      setLiProbeDiag({
        text,
        readOk: Boolean(readOk),
        pending: false,
        at: Date.now(),
        raw: raw || null,
      })
    }
    const runProbe = async () => {
      if (cancelled) return
      if (probeInFlightRef.current) return
      if (probeFailCountRef.current >= 2) return
      probeInFlightRef.current = true
      try {
        if (!isNexusLinkedInExtensionInstalled()) {
          probeFailCountRef.current += 1
          applyFinal(
            'NO leyó: falta la extensión Nexus en este Chrome (recargala en chrome://extensions).',
            false,
          )
          return
        }

        let target = null
        try {
          const checks = await fetchLinkedInPendingConnectChecks(companyId)
          const items = Array.isArray(checks?.items) ? checks.items : []
          const first = items[0]
          if (first?.prospect_id && first?.linkedin_url) {
            target = {
              prospectId: Number(first.prospect_id),
              name: String(first.prospect_name || `Prospecto #${first.prospect_id}`),
              linkedinUrl: String(first.linkedin_url).trim(),
            }
            setLiVerifyTarget(target)
          }
        } catch (e) {
          probeFailCountRef.current += 1
          applyFinal(
            `NO leyó: no pude pedir la cola (${e instanceof Error ? e.message : String(e)})`,
            false,
          )
          return
        }

        if (cancelled) return
        if (!target) {
          probeFailCountRef.current += 1
          applyFinal('NO leyó: no hay prospecto en checking con secuencia iniciada.', false)
          if (!cancelled) void load()
          return
        }

        setLiProbeHint(`Leyendo grado de ${target.name}…`)
        setLiProbeDiag({
          text: `Leyendo… · ${target.name}`,
          readOk: false,
          pending: true,
          at: Date.now(),
        })

        const waitPromise = waitForLinkedInConnectionStatus(target.prospectId, 28_000)
        let one
        try {
          one = await probeLinkedInConnectionViaExtension({
            profileUrl: target.linkedinUrl,
            prospectId: target.prospectId,
            prospectName: target.name,
            connectionStatus: 'checking',
            timeoutMs: 40_000,
          })
        } catch (e) {
          one = { ok: false, readOk: false, error: e instanceof Error ? e.message : String(e) }
        }
        if (cancelled) return

        const returned = String(
          one?.connectionStatus || one?.connection_status || one?.verdict || one?.status || '',
        ).toLowerCase()
        if (returned === 'connected' || returned === 'not_connected' || returned === 'invite_pending') {
          try {
            if (!one?.reported && (returned === 'connected' || returned === 'not_connected')) {
              await reportLinkedInConnectionStatus(target.prospectId, returned)
            }
          } catch {
            /* ignore */
          }
          probeFailCountRef.current = 0
          const okText =
            returned === 'connected'
              ? `SÍ leyó · ${target.name}: 1º (contacto) → Enviar mensaje`
              : `SÍ leyó · ${target.name}: ${Number(one?.degree) === 3 ? '3º' : '2º'} (no contacto) → Enviar Conectar`
          applyFinal(okText, true, one)
          if (!cancelled) void load()
          return
        }

        const waited = await waitPromise
        if (cancelled) return
        if (waited === 'connected' || waited === 'not_connected' || waited === 'invite_pending') {
          probeFailCountRef.current = 0
          const okText =
            waited === 'connected'
              ? `SÍ leyó · ${target.name}: 1º (contacto) → Enviar mensaje`
              : `SÍ leyó · ${target.name}: 2º/3º (no contacto) → Enviar Conectar`
          applyFinal(okText, true)
          void load()
          return
        }

        probeFailCountRef.current += 1
        const err = String(one?.error || one?.reason || 'no_degree').trim()
        const csrf = one?.csrf === false ? ' · sin cookie JSESSIONID (abrí LinkedIn logueado)' : ''
        const via = one?.via ? ` · ${one.via}` : ''
        const attempts = Array.isArray(one?.attempts)
          ? ` · intentos: ${one.attempts.map((a) => `${a.step}${a.ok ? '✓' : '✗'}`).join(',')}`
          : ''
        applyFinal(`NO leyó · ${target.name}: ${err}${via}${csrf}${attempts}`, false, one)
        if (!cancelled) void load()
      } finally {
        probeInFlightRef.current = false
      }
    }
    const t0 = window.setTimeout(() => {
      void runProbe()
    }, isNewWave ? 400 : 800)
    const t1 = window.setTimeout(() => {
      if (probeFailCountRef.current < 2) void runProbe()
    }, 40_000)
    return () => {
      cancelled = true
      window.clearTimeout(t0)
      window.clearTimeout(t1)
    }
  }, [freeze, companyId, sequenceStartedByUser, liQueue.pending_verify, load])

  const stats = outreach?.stats ?? {
    contacted: 0,
    responded: 0,
    interested: 0,
    not_interested: 0,
    failed: 0,
    total_prospects: 0,
    prospects_pending_contact: 0,
    messages_outbound: 0,
    messages_inbound: 0,
  }
  const totalProspects = Number(stats.total_prospects) || list.length
  const prospectsPending =
    Number(stats.prospects_pending_contact) || Math.max(0, totalProspects - (stats.contacted || 0))
  const messagesOutbound = Number(stats.messages_outbound) || 0
  const messagesInbound = Number(stats.messages_inbound) || 0
  const liPending = Number(liQueue.total_pending) || (liQueue.tasks ?? []).length
  const liVerifying = Number(liQueue.pending_verify) || 0
  const waPending = Number(waQueue.total_pending) || (waQueue.tasks ?? []).length
  const realMode = outreach?.real_mode === true
  const allowedChannels = Array.isArray(campaign?.allowed_channels)
    ? campaign.allowed_channels.map((c) => String(c || '').toLowerCase())
    : []
  const emailAllowed = allowedChannels.includes('email')
  const linkedinAllowed = allowedChannels.includes('linkedin')
  const whatsappAllowed =
    allowedChannels.includes('whatsapp') ||
    allowedChannels.includes('wa') ||
    allowedChannels.includes('phone')
  const extensionMissingLocal =
    !LI_SAFE_NO_PROFILE_PROBE &&
    linkedinAllowed &&
    (liVerifying > 0 || liPending > 0) &&
    !isNexusLinkedInExtensionInstalled()
  const whatsappExtensionMissingLocal =
    whatsappAllowed && waPending > 0 && !isNexusWhatsAppExtensionReady()
  const gmailMissingLocal = running && emailAllowed && gmailConnected === false
  const linkedinStallLocal =
    !LI_SAFE_NO_PROFILE_PROBE &&
    linkedinAllowed &&
    liVerifying > 0 &&
    isNexusLinkedInExtensionInstalled()
  const rawSequenceBlock = sequenceBlock?.error
    ? sequenceBlock
    : extensionMissingLocal
      ? {
          channel: 'linkedin',
          code: 'extension_not_installed',
          error:
            'LinkedIn está abierto, pero Nexus no detecta la extensión en este navegador. Instalá/activá la extensión Nexus en Chrome y recargá esta página. No hace falta saltar LinkedIn.',
          action: 'reconnect_extension',
        }
      : whatsappExtensionMissingLocal
        ? {
            channel: 'whatsapp',
            code: 'extension_not_installed',
            error:
              'Extensión Nexus de WhatsApp no detectada en este navegador. Instalá/reactivá la extensión para enviar.',
            action: 'reconnect_extension',
          }
        : gmailMissingLocal
          ? {
              channel: 'email',
              code: 'gmail_disconnected',
              error:
                'Gmail del vendedor no está conectado. Andá a Integraciones y reconectá tu cuenta Google.',
              action: 'reconnect_gmail',
            }
          : linkedinStallLocal
            ? {
                channel: 'linkedin',
                code: 'extension_not_responding',
                error:
                  liProbeHint ||
                  'Hay contactos en verificación LinkedIn. Dejá LinkedIn abierto y logueado en la misma Chrome donde está la extensión Nexus; Nexus reintenta sola cada pocos segundos.',
                action: 'reconnect_extension',
              }
            : null
  // LI-SAFE: no mostrar banners de verify 1º/2º/3º (ruido legacy).
  const effectiveSequenceBlock =
    LI_SAFE_NO_PROFILE_PROBE &&
    rawSequenceBlock &&
    (String(rawSequenceBlock.code || '') === 'extension_not_responding' ||
      String(rawSequenceBlock.error || '').includes('1º/2º/3º') ||
      String(rawSequenceBlock.error || '').includes('verificando'))
      ? null
      : rawSequenceBlock
  const contactPct =
    totalProspects > 0 ? Math.min(100, Math.round(((stats.contacted || 0) / totalProspects) * 100)) : 0
  const lastSummary = campaign?.autopilot_last_cycle_summary
  const campaignName = String(campaign?.name || '').trim()
  const isIndividualContainer =
    campaignName === 'Secuencias individuales' ||
    campaignName.startsWith('Nexus · Secuencias individuales')

  const pendingChannelEnrich = useMemo(() => {
    const plan = campaign?.sequence_plan
    const steps = Array.isArray(plan?.steps) ? plan.steps : []
    const needed = new Set()
    for (const step of steps) {
      const ch = String(step?.channel || '')
        .trim()
        .toLowerCase()
      if (ch === 'email') needed.add('email')
      else if (ch === 'linkedin') needed.add('linkedin')
      else if (ch === 'whatsapp' || ch === 'wa' || ch === 'phone') needed.add('phone')
    }
    if (needed.size === 0) {
      // Sin plan explícito: si ya está searching, igual mostramos countdown.
      return list.filter((p) => String(p?.channel_enrich_status || '').toLowerCase() === 'searching')
    }
    return list.filter((p) => {
      const st = String(p?.channel_enrich_status || '').toLowerCase()
      if (st === 'searching') return true
      if (st === 'done' || st === 'timed_out' || st === 'skipped') return false
      if (p?.sequence_started_at) return false
      const missing = []
      if (needed.has('email') && !(String(p?.email || '').includes('@'))) missing.push('email')
      if (
        needed.has('linkedin') &&
        !(String(p?.linkedin_url || '').toLowerCase().includes('linkedin.com/in'))
      ) {
        missing.push('linkedin')
      }
      if (needed.has('phone') && !hasUsableWhatsApp(p?.phone, p?.whatsapp)) {
        missing.push('phone')
      }
      return missing.length > 0
    })
  }, [list, campaign?.sequence_plan])

  // Quitar countdown de start cuando ya no hay búsquedas pendientes.
  useEffect(() => {
    if (!startEnrichWait) return undefined
    if (busyAction === 'toggle') return undefined
    if (pendingChannelEnrich.length === 0) {
      setStartEnrichWait(null)
    }
    return undefined
  }, [pendingChannelEnrich, startEnrichWait, busyAction])

  useEffect(() => {
    if (!running || freeze || !campaignId) {
      return undefined
    }
    const id = setInterval(() => {
      void load()
    }, 30_000)
    return () => clearInterval(id)
  }, [running, freeze, campaignId, load])

  // Tras iniciar: refrescar más seguido mientras llegan prospectos del sourcing en background.
  useEffect(() => {
    if (!running || freeze || !campaignId || !activationNote) {
      return undefined
    }
    if (!/segundo plano|buscando|importa/i.test(activationNote)) {
      return undefined
    }
    const started = Date.now()
    const id = setInterval(() => {
      if (Date.now() - started > 3 * 60_000) {
        clearInterval(id)
        return
      }
      void load()
    }, 8_000)
    return () => clearInterval(id)
  }, [running, freeze, campaignId, activationNote, load])

  function formatActivationResult(res) {
    const drafts = Number(res?.drafts) || 0
    const sent = Number(res?.sent) || 0
    const contacted = Number(res?.contacted_now) || 0
    const errs = Array.isArray(res?.error_messages) ? res.error_messages : []
    const enrichPending = Number(res?.channel_enrich_pending) || 0
    const parts = [
      isIndividualContainer
        ? enrichPending > 0
          ? 'Secuencia iniciada — buscando datos faltantes antes del primer toque.'
          : 'Secuencia individual en marcha — solo envíos, sin búsqueda de prospectos.'
        : 'Campaña iniciada — ya está en marcha.',
    ]
    if (enrichPending > 0) {
      parts.push(
        enrichPending === 1
          ? 'completando canales en Prospeo (puede tardar hasta 2 min)'
          : `buscando datos de ${enrichPending} contactos (puede tardar hasta 2 min)`,
      )
    }
    const sourcingMsg = (res?.sourcing_message || '').trim()
    const quotaMet = Boolean(res?.sourcing_quota_met)
    const afterCount = Number(res?.sourcing_prospect_count_after)
    const targetCount = Number(res?.sourcing_prospect_count_target)
    const hasProspects =
      (Number.isFinite(afterCount) && afterCount > 0) ||
      (Number(stats?.total_prospects) || 0) > 0 ||
      list.length > 0
    if (!isIndividualContainer) {
      if (res?.sourcing_queued) {
        parts.push('buscando e importando prospectos en segundo plano')
      } else if (quotaMet) {
        if (Number.isFinite(afterCount) && Number.isFinite(targetCount) && targetCount > 0) {
          parts.push(`Cupo completo ${afterCount}/${targetCount}`)
        } else {
          parts.push('Cupo de prospectos completo')
        }
        parts.push(
          'día 1 LinkedIn: verificando si son contacto (abrí LinkedIn con la extensión Nexus)',
        )
      } else if (res?.sourcing_ran) {
        const imported = Number(res?.sourcing_imported) || 0
        if (Number.isFinite(afterCount) && Number.isFinite(targetCount) && targetCount > 0) {
          parts.push(`Cupo: ${afterCount}/${targetCount} importados`)
        } else if (imported > 0) {
          parts.push(
            `${imported} prospecto${imported === 1 ? '' : 's'} encontrados e importados automáticamente`,
          )
        } else if (sourcingMsg) {
          parts.push(sourcingMsg)
        }
      } else if (sourcingMsg) {
        parts.push(sourcingMsg)
      }
    }
    if (res?.used_gmail) {
      if (drafts > 0) {
        parts.push(`${drafts} borrador${drafts === 1 ? '' : 'es'} en Gmail`)
      }
      if (sent > 0) {
        parts.push(`${sent} email${sent === 1 ? '' : 's'} enviado${sent === 1 ? '' : 's'}`)
      }
      if (contacted === 0 && drafts === 0 && sent === 0 && !hasProspects && !quotaMet) {
        parts.push(
          'secuencia en marcha — Nexus contactará automáticamente cuando haya prospectos en la campaña',
        )
      } else if (contacted === 0 && drafts === 0 && sent === 0 && hasProspects) {
        parts.push('secuencia en marcha — toques según el plan (LinkedIn / WhatsApp / email)')
      }
    } else if (contacted > 0) {
      parts.push(`${contacted} primeros contactos (modo simulación en BD)`)
    } else if (res?.gmail_connected === false) {
      parts.push('conectá Gmail del vendedor para outreach real automático')
    } else {
      parts.push(
        'secuencia en marcha — Nexus procesará contactos y follow-ups en automático',
      )
    }
    if (errs.length > 0) {
      parts.push(`avisos: ${errs[0]}`)
    }
    return parts.join(' · ')
  }

  async function handleStartCampaign() {
    if (!campaign?.seller_id) {
      setError('La campaña no tiene vendedor asignado. Editá la campaña o contactá a tu manager.')
      return
    }
    setBusyAction('toggle')
    setError(null)
    setActivationNote(null)

    const enrichTargets = pendingChannelEnrich
    if (enrichTargets.length > 0) {
      const first = enrichTargets[0]
      const labels = []
      const plan = campaign?.sequence_plan
      const steps = Array.isArray(plan?.steps) ? plan.steps : []
      const needed = new Set(
        steps.map((s) => String(s?.channel || '').trim().toLowerCase()).filter(Boolean),
      )
      if (needed.has('email') || steps.some((s) => s?.channel === 'email')) {
        if (!(String(first?.email || '').includes('@'))) labels.push('email')
      }
      if (
        needed.has('linkedin') ||
        steps.some((s) => String(s?.channel || '').toLowerCase() === 'linkedin')
      ) {
        if (!(String(first?.linkedin_url || '').toLowerCase().includes('linkedin.com/in'))) {
          labels.push('LinkedIn')
        }
      }
      if (
        needed.has('whatsapp') ||
        needed.has('wa') ||
        needed.has('phone') ||
        steps.some((s) => ['whatsapp', 'wa', 'phone'].includes(String(s?.channel || '').toLowerCase()))
      ) {
        if (!(String(first?.phone || first?.whatsapp || '').trim())) labels.push('WhatsApp')
      }
      const deadlineAt = first?.channel_enrich_deadline_at || null
      let maxSeconds = 120
      if (deadlineAt) {
        const rem = Math.ceil((new Date(deadlineAt).getTime() - Date.now()) / 1000)
        if (Number.isFinite(rem) && rem > 5) maxSeconds = Math.min(180, Math.max(rem, 30))
      } else {
        // Deadline lo setea el backend al iniciar; contamos 2 min desde ahora.
        maxSeconds = 120
      }
      setStartEnrichWait({
        label: labels.length
          ? `Buscando datos faltantes (${labels.join(', ')})…`
          : first?.channel_enrich_message || 'Buscando datos faltantes…',
        detail:
          enrichTargets.length > 1
            ? `Completando canales de ${enrichTargets.length} contactos antes de arrancar.`
            : 'Nexus consulta Prospeo; después arranca la secuencia con el plan indicado.',
        deadlineAt: deadlineAt || new Date(Date.now() + maxSeconds * 1000).toISOString(),
        maxSeconds,
      })
    } else {
      setStartEnrichWait(null)
    }

    try {
      // Arranque rápido: enrich corre en background (no espera 2 min en el HTTP).
      const res = await startCampaignOutreach(campaignId, { timeoutMs: 25000 })
      if (res?.sequence) {
        setOutreach((prev) => ({
          ...(prev && typeof prev === 'object' ? prev : {}),
          sequence: res.sequence,
        }))
      }
      setActivationNote(formatActivationResult(res))
      const errs = Array.isArray(res?.error_messages) ? res.error_messages : []
      if (res?.gmail_connected === false) {
        setError(
          errs[0] ||
            'Gmail del vendedor no está conectado. Andá a Integraciones y reconectá tu cuenta.',
        )
      } else if (errs.length > 0 && Number(res?.contacted_now) === 0 && !Number(res?.channel_enrich_pending)) {
        setError(errs[0])
      }
      onChanged?.({
        status: res?.campaign_status || 'running',
        automation_paused: false,
      })
      await load()
      onChanged?.({
        status: res?.campaign_status || 'running',
        automation_paused: false,
      })
      // Si el backend diferió el enrich, mantener countdown hasta que deje de buscar.
      if (Number(res?.channel_enrich_pending) > 0 || enrichTargets.length > 0) {
        /* startEnrichWait se limpia cuando pendingChannelEnrich vacía */
      } else {
        setStartEnrichWait(null)
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      const startInProgress =
        /sigue arrancando|sigue en curso|tardando más de lo habitual|gateway timeout|ocupado|504/i.test(
          msg,
        )
      if (startInProgress) {
        setError(null)
        setActivationNote(
          'Campaña en marcha: Nexus busca e importa prospectos en segundo plano. Los mensajes se preparan a medida que llegan.',
        )
        onChanged?.({ status: 'running', automation_paused: false })
        void load()
      } else if (enrichTargets.length > 0 || startEnrichWait) {
        setActivationNote(
          'Seguimos buscando datos faltantes. Mirando el contador arriba; la secuencia arranca sola cuando termine.',
        )
        setError(null)
      } else {
        setError(msg)
        setStartEnrichWait(null)
      }
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
      setActivationNote('Secuencia pausada. Nexus no enviará ni hará follow-ups hasta que la reanudes.')
      await load()
      onChanged?.({ status: 'paused', automation_paused: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction('')
    }
  }

  async function handleContinueWithoutBlockedChannel() {
    const channel = String(effectiveSequenceBlock?.channel || sequenceBlock?.channel || '')
      .trim()
      .toLowerCase()
    if (!channel || !campaignId || skipChannelBusy) return
    const label =
      channel === 'linkedin'
        ? 'LinkedIn'
        : channel === 'whatsapp'
          ? 'WhatsApp'
          : channel === 'email'
            ? 'email'
            : channel
    const exactError = String(effectiveSequenceBlock?.error || sequenceBlock?.error || '').trim()
    const ok = window.confirm(
      [
        `¿Seguir la secuencia sin ${label}?`,
        '',
        exactError ? `Error que frena ahora: ${exactError}` : null,
        '',
        `Consecuencias: se omiten los toques de ${label} de esta campaña y la secuencia continúa solo con los otros canales del plan.`,
        'Si más adelante reconectás la integración, podés volver a habilitar el canal editando la campaña.',
      ]
        .filter(Boolean)
        .join('\n'),
    )
    if (!ok) return
    setSkipChannelBusy(true)
    setError(null)
    try {
      const res = await continueCampaignWithoutChannel(campaignId, channel, { confirm: true })
      setSequenceBlock(null)
      setActivationNote(res?.message || `Secuencia sin ${label}: continuando con el resto del plan.`)
      setProgressNote(res?.message || null)
      onChanged?.({
        allowed_channels: Array.isArray(res?.allowed_channels) ? res.allowed_channels : undefined,
      })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSkipChannelBusy(false)
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
      const msg = e instanceof Error ? e.message : String(e)
      if (e instanceof Error && e.name === 'AbortError') {
        setError(
          'La solicitud tardó mucho. Revisá Gmail: el correo pudo haberse enviado igual. Si llegó, usá Sincronizar respuestas.',
        )
      } else {
        setError(msg)
      }
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
      const created = Number(res?.created) || 0
      if (created > 0) {
        notifyMeetingsChanged({ created })
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
      linkedin_profile_urn: task.linkedin_profile_urn || null,
      linkedin_assisted_draft: task.message,
    }
  }

  async function handleAbrirLinkedIn(input) {
    const p = input?.prospect_id ? prospectFromTask(input) : input
    if (!p) {
      return
    }
    setError(null)
    if (!hasRealLinkedInUrl(p.linkedin_url)) {
      setError(
        `${p.name} no tiene un perfil LinkedIn real. Configurá linkedin.com/in/... válido.`,
      )
      return
    }
    const profileUrl = linkedInOpenUrl(p.linkedin_url)
    const chatUrl = linkedInMessageOpenUrl({
      linkedinUrl: p.linkedin_url,
      linkedinProfileUrn:
        input?.linkedin_profile_urn ||
        p.linkedin_profile_urn ||
        null,
    })
    const textHint = (
      (input?.message || '').trim() ||
      (p.linkedin_assisted_draft || '').trim() ||
      ''
    )
    const isReply = Boolean(input?.is_reply)
    const hasExt = isNexusLinkedInExtensionInstalled()
    const openTarget = chatUrl || profileUrl

    setLiBusy(p.id)
    try {
      // 1) Sesión + texto final PRIMERO (sin abrir pestañas todavía).
      let text = textHint
      let sessionId = null
      try {
        const res = await beginLinkedInAssistedSession(p.id)
        text = (res?.message || textHint).trim()
        sessionId = res?.session_id || null
      } catch (beginErr) {
        console.warn('[LinkedIn] begin session', beginErr)
      }

      const clipboardOk = await copyTextToClipboard(text)

      if (text && profileUrl) {
        void syncLinkedInPendingToExtension({
          profileUrl,
          message: text,
          prospectId: p.id,
          isReply,
        }).catch(() => {})
      }

      // 2) UNA sola apertura (antes: window.open + 2× armOpenChat = 2-3 pestañas + “reload”).
      let assist = null
      if (hasExt && profileUrl) {
        assist = await assistLinkedInOnExistingTabViaExtension({
          profileUrl,
          message: text,
          sessionId,
          prospectId: p.id,
          isReply,
          openChatOnly: false,
          // Una pestaña alcanza: reusa si hay LI, si no crea una sola.
          adoptOnly: false,
        })
      } else if (openTarget) {
        window.open(openTarget, 'nexus_linkedin_assist')
        assist = { ok: true, mode: 'extension-chat-open', method: 'window-open' }
      }

      const pasted = Boolean(assist?.pasted || assist?.mode === 'extension')
      const openedCompose = Boolean(chatUrl && String(chatUrl).includes('/messaging/compose'))
      const chatReady =
        pasted ||
        openedCompose ||
        (Boolean(assist?.ok) &&
          (assist?.mode === 'extension' ||
            assist?.mode === 'extension-chat-open' ||
            Boolean(assist?.composeUrl)))

      setActivationNote(
        pasted
          ? 'Mensaje pegado en LinkedIn. Revisá y enviá con Enter — Nexus lo detecta sola.'
          : chatReady
            ? 'Chat abierto. Si el renglón está vacío, pegá con Ctrl+V.'
            : hasExt
              ? 'LinkedIn abierto. Nexus está abriendo el chat y pegando el mensaje…'
              : 'Perfil abierto. Tocá Mensaje y pegá con Ctrl+V.',
      )

      setLiModal({
        open: true,
        prospect: p,
        text: text || textHint,
        clipboardOk,
        sessionStarted: Boolean(sessionId),
        chatReady,
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
    setLiModal({
      open: false,
      prospect: null,
      text: '',
      clipboardOk: false,
      sessionStarted: false,
      chatReady: false,
    })
  }

  async function handleAbandonLiModal() {
    const p = liModal.prospect
    setLiModal({
      open: false,
      prospect: null,
      text: '',
      clipboardOk: false,
      sessionStarted: false,
      chatReady: false,
    })
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

  async function handleRegenerateLiReply(task) {
    const prospectId = task?.prospect_id
    if (!prospectId) {
      return
    }
    setLiRegenerating(prospectId)
    setError(null)
    try {
      await regenerateLinkedInAssistedReply(prospectId)
      notifyLinkedInQueueChanged()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiRegenerating(null)
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
    const prospectId = p.id
    setLiBusy(prospectId)
    setLiQueue((prev) => {
      const tasks = (prev.tasks || []).filter((t) => Number(t.prospect_id) !== Number(prospectId))
      return { ...prev, tasks, total_pending: tasks.length }
    })
    try {
      await markLinkedInAssistedSent(prospectId)
      clearLiContactarDone(prospectId)
      clearLiRespondieronDismiss(prospectId)
      setLiContactarTick((n) => n + 1)
      setLiRespondieronDismissTick((n) => n + 1)
      setLiModal({ open: false, prospect: null, text: '', clipboardOk: false, sessionStarted: false, chatReady: false })
      setActivationNote(
        'LinkedIn marcado enviado. La secuencia sigue; si te responden, usá Respondieron y pegá el mensaje.',
      )
      notifyLinkedInQueueChanged()
      onChanged?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      await load()
    } finally {
      setLiBusy(null)
    }
  }

async function handleAbrirWhatsAppWeb(task) {
    const prospectId = Number(task?.prospect_id || 0)
    if (!prospectId) return
    setWaBusy(prospectId)
    setError(null)
    try {
      const res = await beginWhatsAppAssistedSession(prospectId)
      const text = (res?.message || task?.message || '').trim()
      const phone = res?.phone_digits || task?.phone_digits || ''
      if (!phone) {
        setError('No hay teléfono válido para abrir WhatsApp Web.')
        return
      }
      if (text) await copyTextToClipboard(text)
      if (isNexusWhatsAppExtensionReady()) {
        // Extensión: abrir chat SIN ?text= (evita basura/truncado) y pegar el borrador exacto.
        const sendUrl = waWebSendUrl(phone, '') || `https://web.whatsapp.com/send?phone=${phone}`
        void armWhatsAppOpenChatViaExtension({
          sendUrl,
          prospectId,
          message: text,
          phoneDigits: phone,
          prospectName: task?.prospect_name || '',
        })
      } else {
        // Sin extensión: no usar ?text= (WhatsApp Web trunca). Clipboard + Ctrl+V.
        const sendUrlClean = waWebSendUrl(phone, '') || `https://web.whatsapp.com/send?phone=${phone}`
        window.open(sendUrlClean, 'nexus_whatsapp_assist')
        setActivationNote(
          'WhatsApp Web abierto. Pegá con Ctrl+V (el mensaje está en el portapapeles) y enviá.',
        )
        await load()
        return
      }
      setActivationNote('WhatsApp Web: revisá el mensaje completo y enviá con Enter.')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setWaBusy(null)
    }
  }

  async function handleMarkWaSent(task) {
    const prospectId = Number(task?.prospect_id || 0)
    if (!prospectId) return
    setWaBusy(prospectId)
    setWaQueue((prev) => {
      const tasks = (prev.tasks || []).filter((t) => Number(t.prospect_id) !== prospectId)
      return { ...prev, tasks, total_pending: tasks.length }
    })
    try {
      await markWhatsAppAssistedSent(prospectId)
      notifyWhatsAppQueueChanged({ sent: true, prospectId })
      onChanged?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      await load()
    } finally {
      setWaBusy(null)
    }
  }

  async function handleSendConnect(task) {
    const p = task?.prospect_id ? prospectFromTask(task) : task
    if (!p) return
    if (!hasRealLinkedInUrl(p.linkedin_url)) {
      setError(`${p.name} no tiene un perfil LinkedIn real. Configurá linkedin.com/in/... válido.`)
      return
    }
    setError(null)
    const profileUrl = linkedInOpenUrl(p.linkedin_url)

    // Solo el perfil: el humano hace Contactar en LinkedIn (sin custom-invite).
    // No re-probear grado: el veredicto ya está (invite_pending / Conectar).
    if (profileUrl) window.open(profileUrl, 'nexus_linkedin_assist')
    setLiProbeHint(null)
    setLiProbeDiag(null)
    setLiVerifyTarget(null)
    setActivationNote(
      'Se abrió el perfil en LinkedIn. Tocá Contactar vos, y después «Ya envié la solicitud».',
    )
  }

  /** LI-SAFE: un click humano abre el perfil (reusa tab LI); tilde verde local. */
  async function handleLiSafeContactar(task) {
    const p = task?.prospect_id ? prospectFromTask(task) : task
    if (!p) return
    if (!hasRealLinkedInUrl(p.linkedin_url)) {
      setError(`${p.name} no tiene un perfil LinkedIn real. Configurá linkedin.com/in/... válido.`)
      return
    }
    setError(null)
    const profileUrl = linkedInOpenUrl(p.linkedin_url)
    setLiBusy(p.id)
    try {
      await openLiSafeProfile(profileUrl)
      markLiContactarDone(p.id)
      setLiContactarTick((n) => n + 1)
      setActivationNote(
        'Perfil abierto. Si ya habías abierto LinkedIn desde Nexus, reusa esa misma pestaña. Conectá o abrí el chat vos. La tilde de Contactar quedó en verde.',
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiBusy(null)
    }
  }

  /** LI-SAFE: copia + abre perfil (reusa tab). NO saca de la cola (tilde de Enviar). */
  async function handleLiSafeEnviarMensaje(task) {
    const p = task?.prospect_id ? prospectFromTask(task) : task
    if (!p?.id) return
    if (!hasRealLinkedInUrl(p.linkedin_url)) {
      setError(`${p.name} no tiene un perfil LinkedIn real. Configurá linkedin.com/in/... válido.`)
      return
    }
    setError(null)
    const text = (
      (task?.message || '').trim() ||
      (p.linkedin_assisted_draft || '').trim() ||
      ''
    )
    const profileUrl = linkedInOpenUrl(p.linkedin_url)
    setLiBusy(p.id)
    try {
      const clipboardOk = text ? await copyTextToClipboard(text) : false
      await openLiSafeProfile(profileUrl)
      setActivationNote(
        clipboardOk
          ? 'Mensaje copiado y perfil abierto. Pegá con Ctrl+V, enviá, y tocá la tilde gris.'
          : 'Se abrió LinkedIn. Si no se copió, volvé a Nexus y usá Enviar mensaje de nuevo.',
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiBusy(null)
    }
  }

  async function handleMarkConnectSent(task) {
    const p = task?.prospect_id ? prospectFromTask(task) : task
    if (!p?.id) return
    setError(null)
    setLiBusy(p.id)
    try {
      await markLinkedInConnectSent(p.id)
      setLiProbeHint(null)
      setLiProbeDiag(null)
      setLiVerifyTarget(null)
      setActivationNote(
        'Solicitud registrada. El mensaje quedó en la cola: envialo en LinkedIn cuando acepte.',
      )
      await load()
      onChanged?.()
      notifyLinkedInQueueChanged({ connectSent: true, prospectId: Number(p.id) })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiBusy(null)
    }
  }

  const liTasks = liQueue.tasks ?? []
  const waTasks = waQueue.tasks ?? []
  const mailItems = mailQueue.items ?? []

  /** Enviados LI sin reply en cola: el SDR puede registrar Respondieron. */
  const liSentAwaitingHuman = useMemo(() => {
    const replyIds = new Set(
      (liTasks || [])
        .filter((t) => Boolean(t.is_reply) || t.action === 'reply')
        .map((t) => Number(t.prospect_id)),
    )
    void liRespondieronDismissTick
    const now = Date.now()
    const canceled = new Set(['canceled', 'cancelled', 'rejected', 'no_show'])
    /** @type {Map<number, object>} */
    const meetingByProspect = new Map()
    for (const m of campaignMeetings || []) {
      const pid = Number(m?.prospect_id || 0)
      if (!pid) continue
      const status = String(m.meeting_status || '').toLowerCase()
      if (canceled.has(status)) continue
      const start = Date.parse(m.scheduled_for || '')
      if (!Number.isFinite(start)) continue
      const prev = meetingByProspect.get(pid)
      const prevStart = prev ? Date.parse(prev.scheduled_for || '') : 0
      if (!prev || start >= prevStart) meetingByProspect.set(pid, m)
    }

    function meetingPhase(prospectId) {
      const m = meetingByProspect.get(Number(prospectId))
      if (!m) return 'none'
      const start = Date.parse(m.scheduled_for || '')
      const durMin = Math.max(15, Math.min(Number(m.duration_minutes) || 30, 240))
      const end = start + durMin * 60_000
      if (end > now) return 'upcoming'
      return 'ended'
    }

    function sequenceAdvancedAfterLiSent(p) {
      if (Boolean(p.sequence_paused)) return false
      const sentAt = Date.parse(p.linkedin_sdr_marked_sent_at || '')
      if (!Number.isFinite(sentAt)) return false
      const last = Math.max(
        Date.parse(p.last_outbound_at || '') || 0,
        Date.parse(p.last_touch_at || '') || 0,
        Date.parse(p.last_followup_at || '') || 0,
      )
      if (!last) return false
      // mark-sent suele stampiar last_outbound al mismo instante — exigir toque claramente posterior
      return last > sentAt + 90_000
    }

    return (Array.isArray(list) ? list : []).filter((p) => {
      if (!p?.id || replyIds.has(Number(p.id))) return false
      if (isLiRespondieronDismissed(p.id)) return false
      if (!hasRealLinkedInUrl(p.linkedin_url)) return false
      if (String(p.linkedin_assist_status || '').toLowerCase() !== 'sent') return false

      const phase = meetingPhase(p.id)
      if (phase === 'ended') return false
      if (phase === 'upcoming') return true
      if (sequenceAdvancedAfterLiSent(p)) return false
      return true
    })
  }, [list, liTasks, liRespondieronDismissTick, campaignMeetings])

  async function handleHandoffLiRespondieron(prospectOverride) {
    const p = prospectOverride || liRespondieron
    const id = Number(p?.id || 0)
    if (!id) {
      setLiRespondieron(null)
      return
    }
    setLiBusy(id)
    setError(null)
    try {
      try {
        await pauseProspectSequence(id)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (!/secuencia iniciada/i.test(msg)) throw e
      }
      clearProspectExtensionWatch(id)
      handoffLiRespondieron(id, { kind: 'handoff' })
      setLiRespondieronDismissTick((n) => n + 1)
      setLiRespondieron(null)
      setActivationNote(
        `${p.name || 'Prospecto'} en handoff · secuencia pausada. Te encargás vos en LinkedIn.`,
      )
      onChanged?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiBusy(null)
    }
  }

  async function handleSubmitLiRespondieron(message) {
    const p = liRespondieron
    const id = Number(p?.id || 0)
    const name = p?.name || 'prospecto'
    const text = String(message || '').trim()
    if (!id || text.length < 2) return

    // Cerrar al toque: el SDR sigue trabajando mientras Nexus genera.
    setLiRespondieron(null)
    handoffLiRespondieron(id, { kind: 'omit' })
    setLiRespondieronDismissTick((n) => n + 1)
    setError(null)
    setActivationNote(`Generando respuesta para ${name}… podés seguir con otras tareas.`)
    setLiBusy(id)

    try {
      const result = await registerLinkedInInbound(id, { message: text })
      clearLiRespondieronDismiss(id)
      setLiRespondieronDismissTick((n) => n + 1)
      setActivationNote(
        result?.reply_draft_ready
          ? `Listo: respuesta para ${name} en Responder. Secuencia pausada.`
          : result?.echo_ignored
            ? `No registramos ese texto (parecía eco del mensaje enviado).`
            : result?.detail || `Respuesta de ${name} registrada.`,
      )
      notifyLinkedInQueueChanged({ inbound: true, prospectId: id })
      onChanged?.()
      await load()
    } catch (e) {
      clearLiRespondieronDismiss(id)
      setLiRespondieronDismissTick((n) => n + 1)
      setError(e instanceof Error ? e.message : String(e))
      setActivationNote(null)
    } finally {
      setLiBusy(null)
    }
  }

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

  const sequenceMilestones = useMemo(() => campaignMilestones(campaign), [campaign])

  const { counts: milestoneCounts } = useMemo(
    () => milestoneCompletionCounts(sequenceActiveList, campaign),
    [sequenceActiveList, campaign],
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
  const liNotifCount = liTasks.length
  const waNotifCount = waTasks.length
  const mailNotifCount = Number(mailQueue.total) || mailItems.length
  const [liSectionOpen, setLiSectionOpen] = useState(true)
  const [waSectionOpen, setWaSectionOpen] = useState(true)
  const [mailSectionOpen, setMailSectionOpen] = useState(true)

  useEffect(() => {
    if (focusNotificaciones === true || focusNotificaciones === 'linkedin') {
      setLiSectionOpen(true)
    }
    if (focusNotificaciones === 'whatsapp') {
      setWaSectionOpen(true)
    }
    if (focusNotificaciones === 'mail') {
      setMailSectionOpen(true)
    }
  }, [focusNotificaciones])

  return (
    <div className="space-y-4">
      <CampaignSetupChecklist
        campaign={campaign}
        companyId={companyId}
        sequenceRunning={Boolean(running)}
      />

      <section className="overflow-hidden rounded-2xl border border-nx-border/90 bg-white shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-nx-border bg-gradient-to-br from-zinc-50 to-white px-5 py-5">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-nx-ink">Secuencia automática</h2>
              {running ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-0.5 text-[11px] font-semibold text-red-900 ring-1 ring-red-200/90">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400/70 opacity-60" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
                  </span>
                  En marcha
                </span>
              ) : (
                <span className="rounded-full bg-nx-card-muted px-2.5 py-0.5 text-[11px] font-medium text-nx-muted ring-1 ring-nx-border/80">
                  Detenida
                </span>
              )}
            </div>
            {isIndividualContainer ? (
              <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-nx-ink">
                Secuencia individual: solo envíos al contacto cargado. No busca prospectos nuevos.
              </p>
            ) : null}
          </div>
          {running ? (
            <button
              type="button"
              disabled={freeze || loading || busyAction !== '' || !campaignId}
              className="shrink-0 rounded-xl border border-nx-border-strong bg-white px-5 py-2.5 text-sm font-semibold text-nx-ink shadow-sm hover:bg-nx-card-muted disabled:opacity-40"
              onClick={() => void handleToggleCampaign()}
            >
              {busyAction === 'toggle' ? 'Pausando…' : 'Pausar secuencia'}
            </button>
          ) : (
            <PremiumGradientButton
              disabled={freeze || loading || busyAction !== '' || !campaignId}
              onClick={() => void handleToggleCampaign()}
            >
              {busyAction === 'toggle'
                ? startEnrichWait
                  ? 'Buscando datos…'
                  : isIndividualContainer
                    ? 'Arrancando envíos…'
                    : 'Iniciando campaña…'
                : 'Iniciar secuencia'}
            </PremiumGradientButton>
          )}
        </div>

        {startEnrichWait ? (
          <div className="border-b border-nx-border px-5 py-3">
            <ChannelEnrichCountdown
              active
              label={startEnrichWait.label}
              detail={startEnrichWait.detail}
              deadlineAt={startEnrichWait.deadlineAt}
              maxSeconds={startEnrichWait.maxSeconds}
            />
          </div>
        ) : null}

        <div className="grid gap-3 px-5 py-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <div className="rounded-xl border border-nx-border bg-nx-card-muted/80 px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-ink">
              En esta campaña
            </p>
            <p className="mt-1 text-2xl font-bold tabular-nums text-nx-ink">{totalProspects}</p>
            <p className="mt-0.5 text-[11px] text-nx-ink">
              {stats.contacted || 0} contactado{(stats.contacted || 0) === 1 ? '' : 's'}
              {prospectsPending > 0
                ? ` · ${prospectsPending} pendiente${prospectsPending === 1 ? '' : 's'} de enviar`
                : totalProspects > 0
                  ? ' · todos con al menos un envío'
                  : ''}
            </p>
          </div>
          <div className="rounded-xl border border-nx-border bg-nx-card-muted/80 px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-ink">Mensajes enviados</p>
            <p className="mt-1 text-2xl font-bold tabular-nums text-nx-ink">{messagesOutbound}</p>
            <p className="mt-0.5 text-[11px] text-nx-ink">
              {messagesInbound} respuesta{messagesInbound === 1 ? '' : 's'} · {stats.contacted || 0} prospecto
              {(stats.contacted || 0) === 1 ? '' : 's'} contactado{(stats.contacted || 0) === 1 ? '' : 's'}
            </p>
          </div>
          <div
            className={`rounded-xl border px-4 py-3 ${
              liPending > 0 || liVerifying > 0
                ? 'border-[#0A66C2]/30 bg-[#0A66C2]/5'
                : 'border-nx-border bg-nx-card-muted/80'
            }`}
          >
            <p className="text-[11px] font-semibold uppercase tracking-wide text-[#0A66C2]">
              LinkedIn por enviar
            </p>
            <p
              className={`mt-1 text-2xl font-bold tabular-nums ${
                liPending > 0 || liVerifying > 0 ? 'text-[#0A66C2]' : 'text-nx-ink'
              }`}
            >
              {liPending}
            </p>
            <p className="mt-0.5 text-[11px] text-nx-ink">
              {liVerifying > 0
                ? `${liVerifying} verificando si son contacto${liPending > 0 ? ` · ${liPending} listos` : ''}`
                : liPending > 0
                  ? 'Cola LinkedIn'
                  : 'Cola al día'}
            </p>
          </div>
          <div
            className={`rounded-xl border px-4 py-3 ${
              waPending > 0
                ? 'border-[#25D366]/40 bg-[#25D366]/5'
                : 'border-nx-border bg-nx-card-muted/80'
            }`}
          >
            <p className="text-[11px] font-semibold uppercase tracking-wide text-[#128C7E]">
              WhatsApp por enviar
            </p>
            <p className={`mt-1 text-2xl font-bold tabular-nums ${waPending > 0 ? 'text-[#128C7E]' : 'text-nx-ink'}`}>
              {waPending}
            </p>
            <p className="mt-0.5 text-[11px] text-nx-ink">
              {waPending > 0 ? 'Cola WhatsApp' : 'Cola al día'}
            </p>
          </div>
          <div className="rounded-xl border border-nx-border bg-nx-card-muted/80 px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-ink">Tareas pendientes</p>
            <p className="mt-1 text-2xl font-bold tabular-nums text-nx-ink">{pendingTasks}</p>
            <p className="mt-0.5 text-[11px] text-nx-ink">Follow-ups y revisiones</p>
          </div>
        </div>

        {totalProspects > 0 ? (
          <div className="border-t border-nx-border px-5 pb-5 space-y-4">
            {Number(campaign?.prospect_count) > 0 &&
            !isIndividualContainer &&
            totalProspects < Number(campaign.prospect_count) ? (
              <ProspectQuotaBar
                compact
                current={totalProspects}
                target={campaign.prospect_count}
                hint="Meta ICP de búsqueda (cuántos querés encontrar). Distinto de ‘ya contactados’."
              />
            ) : null}
            <div>
              <div className="flex items-center justify-between text-[11px] font-medium text-nx-ink">
                <span>Progreso de contacto</span>
                <span className="tabular-nums text-nx-ink">{contactPct}%</span>
              </div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-nx-card-muted">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-red-600 to-red-500 transition-all duration-500"
                  style={{ width: `${contactPct}%` }}
                />
              </div>
            </div>
          </div>
        ) : null}
      </section>

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {effectiveSequenceBlock?.error ? (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950 shadow-sm">
          <p className="font-semibold">
            {(() => {
              const ch = String(effectiveSequenceBlock.channel || '')
              const code = String(effectiveSequenceBlock.code || '')
              if (code === 'extension_not_installed' && ch === 'whatsapp') {
                return 'WhatsApp: falta la extensión'
              }
              if (code === 'extension_not_installed' && ch === 'linkedin') {
                return 'LinkedIn: falta la extensión'
              }
              if (code === 'extension_not_responding' && ch === 'linkedin') {
                return 'LinkedIn: la extensión no responde'
              }
              if (code.startsWith('extension')) {
                return ch === 'whatsapp'
                  ? 'WhatsApp: extensión'
                  : 'LinkedIn: extensión'
              }
              const label =
                ch === 'linkedin'
                  ? 'LinkedIn'
                  : ch === 'whatsapp'
                    ? 'WhatsApp'
                    : ch === 'email'
                      ? 'email'
                      : ch
              return label ? `Secuencia en espera · ${label}` : 'Secuencia en espera'
            })()}
          </p>
          <p className="mt-1 leading-relaxed">{effectiveSequenceBlock.error}</p>
          {liProbeHint && effectiveSequenceBlock.channel === 'linkedin' ? (
            <p className="mt-1 text-xs text-amber-900/90">{liProbeHint}</p>
          ) : null}
          <p className="mt-1 text-xs text-amber-900/80">
            {String(effectiveSequenceBlock.code || '') === 'extension_not_installed' &&
            effectiveSequenceBlock.channel === 'whatsapp'
              ? 'Instalá o reactivá la extensión Nexus (Chrome Web Store) y recargá esta página.'
              : String(effectiveSequenceBlock.code || '').includes('extension') &&
                  effectiveSequenceBlock.channel === 'linkedin' &&
                  !LI_SAFE_NO_PROFILE_PROBE
                ? 'Dejá LinkedIn abierto en la misma Chrome con la extensión Nexus.'
                : String(effectiveSequenceBlock.code || '').includes('extension')
                  ? 'Instalá o reactivá la extensión Nexus y recargá esta página.'
                  : 'Reconectá la integración/extensión para continuar.'}
          </p>
          {liVerifying > 0 ? (
            <p className="mt-2 text-xs font-medium text-amber-950">
              {liProbeHint ||
                (liVerifyTarget?.name
                  ? `Verificando automáticamente: ${liVerifyTarget.name}…`
                  : 'Verificando contactos en LinkedIn automáticamente…')}
            </p>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={freeze || skipChannelBusy || !effectiveSequenceBlock.channel}
              className="rounded-lg border border-amber-400/70 bg-white/80 px-3 py-1.5 text-xs font-medium text-amber-900/80 hover:bg-amber-100 disabled:opacity-40"
              onClick={() => void handleContinueWithoutBlockedChannel()}
            >
              {skipChannelBusy
                ? 'Aplicando…'
                : `Último recurso: seguir sin ${
                    effectiveSequenceBlock.channel === 'linkedin'
                      ? 'LinkedIn'
                      : effectiveSequenceBlock.channel === 'whatsapp'
                        ? 'WhatsApp'
                        : effectiveSequenceBlock.channel === 'email'
                          ? 'email'
                          : effectiveSequenceBlock.channel || 'este canal'
                  }`}
            </button>
          </div>
        </div>
      ) : null}

      {!LI_SAFE_NO_PROFILE_PROBE && (liProbeDiag || liVerifying > 0 || liProbeHint) ? (
        <div
          className={`rounded-xl border px-4 py-3 text-sm shadow-sm ${
            liProbeDiag?.readOk
              ? 'border-emerald-300/80 bg-emerald-50 text-emerald-950'
              : liProbeDiag?.pending ||
                  (!liProbeDiag?.readOk &&
                    String(liProbeDiag?.text || liProbeHint || '').startsWith('Leyendo'))
                ? 'border-[#0A66C2]/30 bg-[#0A66C2]/5 text-nx-ink'
                : liProbeDiag && !liProbeDiag.readOk
                  ? 'border-rose-300/80 bg-rose-50 text-rose-950'
                  : 'border-[#0A66C2]/30 bg-[#0A66C2]/5 text-nx-ink'
          }`}
        >
          <p className="font-semibold">
            {liProbeDiag?.readOk
              ? 'LinkedIn · lectura OK'
              : liProbeDiag?.pending ||
                  String(liProbeDiag?.text || liProbeHint || '').startsWith('Leyendo')
                ? 'LinkedIn · leyendo…'
                : liProbeDiag && !liProbeDiag.readOk
                  ? 'LinkedIn · no pudo leer'
                  : 'LinkedIn verificando automáticamente'}
          </p>
          <p className="mt-1 text-sm font-medium leading-relaxed">
            {liProbeDiag?.text ||
              liProbeHint ||
              (liVerifyTarget?.name
                ? `Leyendo grado de ${liVerifyTarget.name}…`
                : 'La extensión abre el perfil, lee 1º/2º/3º y reporta acá exactamente qué vio.')}
          </p>
          {liProbeDiag?.raw?.attempts?.length ? (
            <p className="mt-1 text-[11px] opacity-80">
              Intentos:{' '}
              {liProbeDiag.raw.attempts
                .map((a) => `${a.step}${a.ok ? '✓' : '✗'}${a.degree != null ? `=${a.degree}` : ''}`)
                .join(' · ')}
            </p>
          ) : null}
        </div>
      ) : null}

      {(() => {
        const happening = activationNote || progressNote
        if (!happening || !running) return null
        if (
          LI_SAFE_NO_PROFILE_PROBE &&
          (/1º\/2º\/3º|verificando .*contacto/i.test(happening) ||
            /Secuencia en espera\s*·\s*LinkedIn/i.test(happening))
        ) {
          return null
        }
        return (
        <div className="rounded-xl border border-nx-border bg-nx-card-muted/70 px-4 py-2.5 text-sm text-nx-ink">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">
            Qué está pasando
          </p>
          <p className="mt-0.5 leading-relaxed">{happening}</p>
        </div>
        )
      })()}

      {showManagerTools ? (
      <CollapsibleSection
        title="Gmail manual"
        subtitle="Solo si querés enviar o sincronizar un correo puntual"
        defaultOpen={false}
        badge={running ? 'Opcional' : undefined}
      >
      <section className="rounded-xl border border-zinc-200 bg-zinc-50/60 p-4">
        <h3 className="text-sm font-semibold text-zinc-950">Gmail en vivo</h3>
        <p className="mt-1 text-xs text-zinc-900/90">
          Lee respuestas nuevas en Gmail, redacta con IA y envía. No hace falta «Sincronizar respuestas» antes.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          {emailProspects.length > 0 ? (
            <div className="flex min-w-[14rem] flex-col gap-1">
              <label htmlFor="nx-gmail-live-prospect" className="text-[11px] font-medium text-zinc-900">
                Prospecto (email)
              </label>
              <select
                id="nx-gmail-live-prospect"
                disabled={freeze || loading || busyAction !== ''}
                className="max-w-sm rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-xs text-nx-ink shadow-sm disabled:opacity-50"
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
          ) : (
            <p className="text-xs text-zinc-900">
              No hay prospectos con email en esta campaña. Agregá un email real al prospecto primero.
            </p>
          )}
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
              className="rounded-lg border border-red-300 bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-700 disabled:opacity-40"
              onClick={() => void handleSendGmailEmail()}
            >
              {busyAction === 'gmail-send' ? 'Enviando…' : 'Enviar email real'}
            </button>
            <button
              type="button"
              disabled={
                freeze || loading || busyAction !== '' || !campaignId || !companyId || !campaign?.seller_id
              }
              className="rounded-lg border border-zinc-300 bg-white px-4 py-2 text-sm font-semibold text-zinc-900 shadow-sm hover:bg-zinc-100 disabled:opacity-40"
              onClick={() => void handleSyncGmailInbound()}
            >
              {busyAction === 'gmail-sync' ? 'Sincronizando…' : 'Sincronizar respuestas'}
            </button>
          </div>
        </div>
      </section>
      </CollapsibleSection>
      ) : null}

      {showOpsDebug ? (
      <div className="flex flex-col gap-3 rounded-lg border border-dashed border-nx-border-strong bg-nx-card-muted/80 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">
          Operaciones manuales (debug)
        </p>
        <div className="flex flex-wrap items-end gap-3">
          {emailProspects.length > 0 ? (
            <div className="flex min-w-[12rem] flex-col gap-1">
              <label htmlFor="nx-gmail-draft-prospect" className="text-[11px] font-medium text-nx-muted">
                Borrador para (email)
              </label>
              <select
                id="nx-gmail-draft-prospect"
                disabled={freeze || loading || busyAction !== ''}
                className="max-w-xs rounded-lg border border-nx-border bg-white px-2 py-1.5 text-xs text-nx-ink shadow-sm disabled:opacity-50"
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
          className="rounded-lg border border-nx-border-strong bg-nx-card-muted px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
          onClick={() => void handleCreateGmailDraft()}
          title="Genera asunto y cuerpo con IA y crea un borrador en Gmail del vendedor. No envía."
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
            !campaign?.seller_id
          }
          className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs font-semibold text-zinc-900 hover:bg-zinc-100 disabled:opacity-40"
          onClick={() => void handleSyncGoogleCalendar()}
          title="Sincroniza eventos de Google Calendar con prospectos de la campaña."
        >
          {busyAction === 'calendar-sync' ? 'Sincronizando…' : 'Sincronizar Calendar'}
        </button>
      </div>
      </div>
      ) : null}

      {showOpsDebug && campaignId && companyId ? (
        <section
          id="nx-debug-calendar"
          className="mt-4 w-full scroll-mt-4 rounded-xl border-2 border-zinc-500 bg-zinc-50 p-4 shadow-md ring-1 ring-zinc-600/20"
          aria-label="Debug Calendar"
        >
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-400/60 pb-3">
            <div className="min-w-[12rem] max-w-2xl">
              <h2 className="text-base font-bold tracking-tight text-zinc-950">Debug Calendar</h2>
              <p className="mt-1 text-xs leading-relaxed text-zinc-900">
                Acá ves el último resultado de <strong>Sincronizar Calendar</strong>: eventos leídos, invitados
                (attendees), si hubo match con un prospecto, <code className="rounded bg-zinc-200/50 px-1">skip_reason</code>,
                reunión creada/actualizada y si el pipeline se actualizó. Se guarda en esta pestaña al recargar.
              </p>
              {!campaign?.seller_id ? (
                <p className="mt-2 text-xs font-medium text-red-800">
                  Esta campaña no tiene vendedor asignado: el botón Sincronizar Calendar no va a funcionar hasta entonces.
                </p>
              ) : null}
              {calendarSyncOk ? (
                <p className="mt-2 text-xs font-semibold text-zinc-950">
                  Resumen: eventos {calendarSyncOk.events_seen ?? 0} · GET ok {calendarSyncOk.events_enriched ?? 0}{' '}
                  · match {calendarSyncOk.matched ?? 0} · meetings creados {calendarSyncOk.created ?? 0} ·
                  pipeline {calendarSyncOk.pipeline_updated ?? 0}
                </p>
              ) : (
                <p className="mt-2 text-xs text-zinc-900">
                  <strong>Todavía no hay datos.</strong> Pulsá el botón violeta <strong>Sincronizar Calendar</strong>{' '}
                  arriba.
                </p>
              )}
            </div>
            <div className="flex flex-shrink-0 flex-wrap items-center gap-2">
              <button
                type="button"
                className="rounded-lg border-2 border-zinc-800 bg-zinc-100 px-3 py-2 text-xs font-bold text-zinc-950 shadow-sm hover:bg-zinc-200"
                onClick={() => setCalendarDebugExpanded((v) => !v)}
              >
                {calendarDebugExpanded ? 'Ocultar Debug Calendar' : 'Ver Debug Calendar'}
              </button>
              {calendarSyncOk ? (
                <button
                  type="button"
                  className="rounded-lg border border-zinc-700 bg-white px-3 py-2 text-xs font-semibold text-zinc-950 hover:bg-zinc-50"
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
              <div className="mt-4 rounded-lg border border-dashed border-zinc-500 bg-white/70 p-4 text-sm text-zinc-950">
                <p className="font-semibold">Esperando un sync de calendario…</p>
                <p className="mt-1 text-xs text-zinc-900">
                  Cuando la sincronización termine bien, vas a ver acá la lista de eventos, attendees y matches.
                  Si falla el API, el error aparece en la banda roja de arriba.
                </p>
              </div>
            )
          ) : null}
        </section>
      ) : null}

      {freeze ? (
        <p className="text-xs text-zinc-800">Seleccioná la empresa correcta en el header.</p>
      ) : null}

      <CollapsibleSection
        title="Actividad y detalle"
        subtitle="Log en vivo, tabla de prospectos y secuencia de 7 toques"
        defaultOpen={false}
        badge={running ? 'En vivo' : undefined}
      >
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <Metric
          label="Respuestas"
          value={stats.responded}
          hint={realMode ? 'Importadas desde Gmail' : 'Prospectos que respondieron'}
        />
        <Metric label="Follow-ups enviados" value={followupsSent} />
        <Metric label="Citas en calendario" value={meetingsN} hint="Reuniones con fecha en Nexus" />
      </div>

      {lastSummary && typeof lastSummary === 'object' && Object.keys(lastSummary).length > 0 ? (
        <p className="text-[11px] text-nx-muted">
          Última ejecución automática:{' '}
          {typeof lastSummary.processed === 'number' ? `${lastSummary.processed} contactos` : null}
          {lastSummary.messages_generated != null ? ` · mensajes ${lastSummary.messages_generated}` : ''}
        </p>
      ) : null}

      {/* A — Actividad Nexus (live) */}
      <div className="overflow-hidden rounded-xl border border-nx-border/90 bg-gradient-to-b from-zinc-50 to-white">
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
            <p className="px-2 py-6 text-center text-sm text-nx-muted">
              Cuando Nexus ejecute ciclos o lleguen respuestas, vas a ver cada movimiento acá con fecha y hora.
            </p>
          ) : (
            <ul className="relative space-y-0 border-l-2 border-red-200/80 pl-4">
              {unifiedFeed.map((row, i) => (
                <li key={`${row.at}-${i}`} className="relative pb-4 pl-1 last:pb-1">
                  <span className="absolute -left-[9px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-nx-brand shadow-sm shadow-red-900/20" />
                  <p className="text-[11px] font-medium text-nx-subtle">{fmtDate(row.at)}</p>
                  <p className="mt-0.5 text-sm leading-snug text-nx-ink">{row.text}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Secuencia en curso */}
      <div className="rounded-xl border border-nx-border bg-white p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-nx-ink">Prospectos en secuencia</h3>
            <p className="mt-0.5 text-xs text-nx-muted">
              {filteredProspects.length} prospectos · {pendingTasks} tareas pendientes
            </p>
          </div>
          {showOpsDebug ? (
          <button
            type="button"
            disabled={freeze || loading || busyAction !== '' || !campaignId}
            className="rounded-lg border border-nx-border bg-white px-3 py-1.5 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
            onClick={() => void handleRunFollowupsWorker()}
          >
            {busyAction === 'followups' ? 'Procesando…' : 'Forzar follow-ups'}
          </button>
          ) : null}
        </div>
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <input
            type="search"
            placeholder="Buscar prospecto…"
            value={sequenceSearch}
            onChange={(e) => setSequenceSearch(e.target.value)}
            className="w-full rounded-lg border border-nx-border bg-white px-3 py-2 text-sm shadow-sm sm:max-w-xs"
          />
          <div className="flex flex-wrap gap-1.5">
            {SEQUENCE_FILTER_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                onClick={() => setSequenceFilter(opt.key)}
                className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 transition ${
                  sequenceFilter === opt.key
                    ? 'bg-nx-ink text-white ring-nx-ink'
                    : 'bg-white text-nx-muted ring-nx-border hover:bg-nx-card-muted'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-1 sm:gap-0">
          {sequenceMilestones.map((d, idx) => (
            <div key={d} className="flex items-center">
              <div
                className={milestoneChannelChipClass(d, campaign, {
                  completed: (milestoneCounts[d] || 0) > 0,
                })}
              >
                <span className="text-[10px] font-semibold uppercase opacity-70">Día</span>
                <span className="text-sm font-bold tabular-nums">{d}</span>
                <span className="mt-0.5 text-[9px] leading-tight opacity-80">
                  {milestoneShortLabel(d, campaign)}
                </span>
                <span className="text-[10px] opacity-70">{milestoneCounts[d] || 0} ok</span>
              </div>
              {idx < sequenceMilestones.length - 1 ? (
                <span className="hidden px-0.5 text-nx-subtle sm:inline" aria-hidden>
                  →
                </span>
              ) : null}
            </div>
          ))}
        </div>

        <div className="mt-5 overflow-x-auto rounded-lg border border-nx-border">
          <table className="min-w-[640px] w-full text-left text-xs">
            <thead className="bg-nx-card-muted text-[10px] font-semibold uppercase tracking-wide text-nx-muted">
              <tr>
                <th className="px-3 py-2">Prospecto</th>
                <th className="px-3 py-2">Último hito</th>
                <th className="px-3 py-2">Próximo paso</th>
                <th className="px-3 py-2">Canal siguiente</th>
                <th className="px-3 py-2">Qué está haciendo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-nx-border">
              {sequenceRows.map(({ p, summary }) => (
                <tr key={p.id} className="hover:bg-nx-card-muted/80">
                  <td className="px-3 py-2">
                    <span className="font-medium text-nx-ink">{p.name}</span>
                    <span className="block text-[11px] text-nx-muted">{p.company_name || '—'}</span>
                  </td>
                  <td className="px-3 py-2 tabular-nums text-nx-ink">
                    {summary.lastCompletedDay > 0 ? `Día ${summary.lastCompletedDay}` : '—'}
                  </td>
                  <td className="max-w-[14rem] px-3 py-2 text-nx-ink">
                    <span className="line-clamp-2" title={summary.line}>
                      {summary.line}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-nx-ink">{summary.channelLabel}</td>
                  <td className="px-3 py-2">
                    <ProspectActivityBadge prospect={p} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sequenceRows.length === 0 ? (
            <p className="border-t border-nx-border px-3 py-6 text-center text-xs text-nx-muted">
              No hay prospectos con este filtro.
            </p>
          ) : null}
        </div>
      </div>

      </CollapsibleSection>

      <CollapsibleSection
        id="campaign-linkedin"
        title="LinkedIn"
        subtitle="Enviar, marcar enviado · si responden, registralo acá"
        tone="linkedin"
        badge={liNotifCount > 0 ? liNotifCount : null}
        open={liSectionOpen}
        onOpenChange={setLiSectionOpen}
      >
      {postergados.length ? (
        <div className="rounded-lg border border-zinc-200 bg-zinc-50/70 p-3">
          <h3 className="text-xs font-semibold text-zinc-950">Postergados</h3>
          <ul className="mt-2 space-y-1 text-xs text-zinc-950">
            {postergados.slice(0, 20).map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {p.name} · {p.company_name}
                  {p.defer_resume_at ? (
                    <span className="ml-1 text-[10px] text-zinc-800/90">
                      · re-contacto ~ {fmtDate(p.defer_resume_at)}
                    </span>
                  ) : null}
                </span>
                <button
                  type="button"
                  disabled={freeze || liBusy === p.id}
                  className="rounded border border-zinc-300 bg-white px-2 py-0.5 text-[11px] font-semibold hover:bg-zinc-100 disabled:opacity-40"
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
        <div className="rounded-lg border border-zinc-100 bg-zinc-50/60 p-3">
          <h3 className="text-xs font-semibold text-zinc-950">Encajonados</h3>
          <ul className="mt-2 space-y-1 text-xs text-zinc-950">
            {encajonados.slice(0, 20).map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {p.name} · {p.company_name}
                </span>
                <button
                  type="button"
                  disabled={freeze || liBusy === p.id}
                  className="rounded border border-zinc-300 bg-white px-2 py-0.5 text-[11px] font-semibold hover:bg-zinc-100 disabled:opacity-40"
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

      {liSentAwaitingHuman.length > 0 ? (
        <div className="mb-5">
          <LinkedInRespondieronPanel
            prospects={liSentAwaitingHuman}
            freeze={freeze}
            busyProspectId={liBusy}
            onOpenRespondieron={(p) => setLiRespondieron(p)}
            onHandoff={(p) => void handleHandoffLiRespondieron(p)}
          />
        </div>
      ) : null}

      {liTasks.length > 0 || (liQueue.hidden_by_cap ?? 0) > 0 || (liQueue.days ?? []).length > 0 ? (
        <LinkedInAssistQueue
          tasks={liTasks}
          days={liQueue.days ?? []}
          freeze={freeze}
          busyProspectId={liBusy}
          invitesRemaining={liQueue.invites_remaining ?? null}
          invitesLimit={liQueue.invites_limit ?? null}
          dmsRemaining={liQueue.dms_remaining ?? null}
          dmsLimit={liQueue.dms_limit ?? null}
          hiddenByCap={liQueue.hidden_by_cap ?? 0}
          contactarTick={liContactarTick}
          onContactar={(task) => handleLiSafeContactar(task)}
          onEnviarMensaje={(task) => void handleLiSafeEnviarMensaje(task)}
          onOpenLinkedIn={(task) => void handleLiSafeEnviarMensaje(task)}
          onMarkSent={(task) => void handleMarkLiSent(task)}
        />
      ) : liSentAwaitingHuman.length === 0 ? (
        <p className="rounded-lg border border-dashed border-[#0A66C2]/30 bg-white/80 px-3 py-4 text-center text-[12px] text-nx-muted">
          Sin mensajes LinkedIn pendientes.
        </p>
      ) : null}
      </CollapsibleSection>

      <CollapsibleSection
        id="campaign-whatsapp"
        title="WhatsApp"
        subtitle="Mensajes listos para enviar por WhatsApp"
        tone="whatsapp"
        badge={waNotifCount > 0 ? waNotifCount : null}
        open={waSectionOpen}
        onOpenChange={setWaSectionOpen}
      >
        <WhatsAppAssistQueue
          tasks={waTasks}
          days={waQueue.days ?? []}
          limit={waQueue.limit ?? null}
          effectiveLimitToday={waQueue.effective_limit_today ?? null}
          bonusFromReplies={waQueue.bonus_from_replies ?? 0}
          remainingToday={waQueue.remaining_today ?? null}
          hiddenByCap={waQueue.hidden_by_cap ?? 0}
          freeze={freeze}
          busyProspectId={waBusy}
          onOpenWhatsAppWeb={(task) => void handleAbrirWhatsAppWeb(task)}
          onMarkSent={(task) => void handleMarkWaSent(task)}
        />
      </CollapsibleSection>

      <CollapsibleSection
        id="campaign-mail"
        title="Mail"
        subtitle="Mails enviados · tocá para ver el mensaje"
        tone="mail"
        badge={mailNotifCount > 0 ? mailNotifCount : null}
        open={mailSectionOpen}
        onOpenChange={setMailSectionOpen}
      >
        <MailSentQueue
          items={mailItems}
          days={mailQueue.days ?? []}
          pendingTotal={mailQueue.pending_total ?? 0}
          limit={mailQueue.limit ?? null}
          remainingToday={mailQueue.remaining_today ?? null}
        />
      </CollapsibleSection>

      {liModal.open ? (
        <Modal
          title={`Mensaje · ${liModal.prospect?.name ?? ''}`}
          onClose={handleCloseLiModal}
          footer={
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded-lg border border-nx-border px-3 py-1.5 text-xs"
                onClick={() => void handleAbandonLiModal()}
              >
                Cerrar sin enviar
              </button>
              <button
                type="button"
                className="rounded-lg border border-nx-border px-3 py-1.5 text-xs text-nx-muted"
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
          <p className="text-[11px] text-nx-muted">
            {liModal.chatReady
              ? 'Chat listo. Si el mensaje está pegado, enviá con Enter en LinkedIn.'
              : liModal.clipboardOk === false
                ? 'Copiá el texto manualmente desde el cuadro.'
                : 'Mensaje en portapapeles. Si el chat no pegó solo, Ctrl+V en LinkedIn.'}
          </p>
          <textarea
            readOnly
            className="mt-1 w-full min-h-[10rem] rounded-lg border border-nx-border bg-nx-card-muted p-2 text-sm"
            value={liModal.text}
          />
        </Modal>
      ) : null}

      {liRespondieron ? (
        <LinkedInRespondieronModal
          key={Number(liRespondieron.id) || 0}
          prospect={liRespondieron}
          busy={Number(liBusy) === Number(liRespondieron.id)}
          onClose={() => setLiRespondieron(null)}
          onHandoff={() => void handleHandoffLiRespondieron()}
          onSubmit={(msg) => void handleSubmitLiRespondieron(msg)}
        />
      ) : null}
    </div>
  )
}
