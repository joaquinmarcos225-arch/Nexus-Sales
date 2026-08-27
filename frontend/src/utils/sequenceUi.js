/**
 * Secuencia Nexus — debe coincidir con `app/core/sequence_playbook.py`,
 * `app/core/sequence_templates.py` y GET /health/sequence-playbook.
 */

import { CHANNEL_LABELS, CHANNEL_ORDER, orderChannels } from './campaignChannels.js'

export const PLAYBOOK_NAME = 'SDR Nexus 7 toques'
export const PLAYBOOK_VERSION = '1.0'

export const SEQUENCE_TOUCH_DAYS = [1, 4, 7, 10, 13, 16, 19]
export const REACTIVATION_DAY = 42
export const PLAYBOOK_LAST_TOUCH_DAY = 19
export const COOLDOWN_START_DAY = 20

/** Fallback legacy: 7 toques + reactivación (solo si no hay plan de campaña). */
export const SEQUENCE_MILESTONES = [...SEQUENCE_TOUCH_DAYS, REACTIVATION_DAY]

/** Esquema antiguo multicanal 21d → playbook actual. */
export const LEGACY_MILESTONE_DAY_MAP = { 14: 13, 18: 16, 21: 19 }

/** Canal primario por día del playbook default (Nexus 7). */
export const PLAYBOOK_CHANNEL_BY_DAY = {
  1: 'email',
  4: 'linkedin',
  7: 'whatsapp',
  10: 'email',
  13: 'linkedin',
  16: 'whatsapp',
  19: 'email',
}

export function normalizeMilestoneDay(day) {
  const d = parseInt(String(day), 10)
  if (!Number.isFinite(d)) {
    return d
  }
  return LEGACY_MILESTONE_DAY_MAP[d] ?? d
}

/**
 * Follow-up / reactivación (día 42) solo si la campaña lo tiene activo.
 */
export function campaignFollowUpEnabled(campaign) {
  if (!campaign) return false
  if (campaign.post_sequence_followup_enabled === false) return false
  const fu = campaign.sequence_plan?.follow_up
  if (fu && typeof fu === 'object' && 'enabled' in fu) {
    return Boolean(fu.enabled)
  }
  return campaign.post_sequence_followup_enabled !== false
}

/**
 * Mapa día→canal del plan fijo. Null en modo IA o sin pasos.
 */
export function planChannelByDay(campaign) {
  const plan = campaign?.sequence_plan
  if (!plan || typeof plan !== 'object') return null
  if (String(plan.mode || 'fixed').toLowerCase() === 'ia') return null
  const steps = Array.isArray(plan.steps) ? plan.steps : []
  /** @type {Record<number, string>} */
  const out = {}
  for (const step of steps) {
    const day = normalizeMilestoneDay(step?.day)
    const channel = String(step?.channel || '')
      .trim()
      .toLowerCase()
    if (!Number.isFinite(day) || day < 1) continue
    if (channel === 'email' || channel === 'linkedin' || channel === 'whatsapp' || channel === 'call') {
      out[day] = channel
    }
  }
  return Object.keys(out).length ? out : null
}

/**
 * Días de toque de la campaña (cadencia Nexus, largo = steps del plan).
 * Alineado a `plan_touch_days` del backend.
 */
export function campaignTouchDays(campaign) {
  const plan = campaign?.sequence_plan
  if (!plan || typeof plan !== 'object') {
    return [...SEQUENCE_TOUCH_DAYS]
  }
  if (String(plan.mode || 'fixed').toLowerCase() === 'ia') {
    return [...SEQUENCE_TOUCH_DAYS]
  }
  const steps = Array.isArray(plan.steps) ? plan.steps : []
  const playbookSet = new Set(SEQUENCE_TOUCH_DAYS)
  const wanted = new Set()
  for (const step of steps) {
    const day = normalizeMilestoneDay(step?.day)
    if (playbookSet.has(day)) wanted.add(day)
  }
  if (wanted.size === 0) {
    return [...SEQUENCE_TOUCH_DAYS]
  }
  return SEQUENCE_TOUCH_DAYS.filter((d) => wanted.has(d))
}

/**
 * Hitos a mostrar en UI: toques del plan + día 42 solo si hay follow-up activo.
 */
export function campaignMilestones(campaign) {
  const days = campaignTouchDays(campaign)
  if (campaignFollowUpEnabled(campaign)) {
    return [...days, REACTIVATION_DAY]
  }
  return days
}

export function parseSequenceFired(raw) {
  let parsed = []
  if (Array.isArray(raw)) {
    parsed = raw.map((x) => parseInt(String(x), 10)).filter((n) => Number.isFinite(n))
  } else {
    try {
      const arr = JSON.parse(String(raw || '[]'))
      parsed = Array.isArray(arr)
        ? arr.map((x) => parseInt(String(x), 10)).filter((n) => Number.isFinite(n))
        : []
    } catch {
      parsed = []
    }
  }
  return [...new Set(parsed.map(normalizeMilestoneDay))].sort((a, b) => a - b)
}

/** Alineado al backend: días calendario UTC entre inicio y hoy, mínimo día 1 si ya empezó. */
export function sequenceCalendarDayIndex(sequenceStartedAtIso) {
  if (!sequenceStartedAtIso) {
    return 0
  }
  try {
    const start = new Date(sequenceStartedAtIso)
    const now = new Date()
    const t0 = Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate())
    const t1 = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
    const delta = Math.floor((t1 - t0) / 86400000)
    return Math.max(1, delta + 1)
  } catch {
    return 0
  }
}

export function orderedAllowedChannels(campaign) {
  const sel = campaign?.allowed_channels
  const o = orderChannels(sel?.length ? sel : CHANNEL_ORDER)
  return o.length ? o : ['email']
}

function channelDay1(allowed) {
  if (allowed.includes('email')) {
    return 'email'
  }
  return allowed[0] || 'email'
}

function channelWaOrMail(prospect, allowed) {
  if ((prospect.phone || prospect.whatsapp_number || '').trim() && allowed.includes('whatsapp')) {
    return 'whatsapp'
  }
  if (allowed.includes('email')) {
    return 'email'
  }
  return allowed[0] || 'email'
}

function channelAvailable(prospect, channel, allowed) {
  const ch = String(channel || '').toLowerCase()
  if (!allowed.includes(ch)) {
    return false
  }
  if (ch === 'email') {
    return Boolean((prospect.email || '').trim() && (prospect.email || '').includes('@'))
  }
  if (ch === 'linkedin') {
    return Boolean((prospect.linkedin_url || '').trim())
  }
  if (ch === 'whatsapp') {
    return Boolean((prospect.phone || prospect.whatsapp_number || prospect.whatsapp || '').trim())
  }
  if (ch === 'call') {
    return Boolean(
      (prospect.landline_phone || prospect.phone || prospect.whatsapp || prospect.whatsapp_number || '').trim(),
    )
  }
  return false
}

function fallbackChannel(primary, prospect, allowed) {
  if (primary === 'linkedin' || primary === 'whatsapp') {
    if (channelAvailable(prospect, 'email', allowed)) {
      return 'email'
    }
  }
  for (const ch of allowed) {
    if (channelAvailable(prospect, ch, allowed)) {
      return ch
    }
  }
  return primary
}

export function milestoneChannel(m, prospect, campaign) {
  const day = normalizeMilestoneDay(m)
  const allowed = orderedAllowedChannels(campaign)

  if (day === REACTIVATION_DAY) {
    const fu = campaign?.sequence_plan?.follow_up
    const fuCh = String(fu?.channel || 'auto').toLowerCase()
    if (fuCh === 'email' || fuCh === 'linkedin' || fuCh === 'whatsapp') {
      if (channelAvailable(prospect, fuCh, allowed)) return fuCh
      return fallbackChannel(fuCh, prospect, allowed)
    }
    return channelWaOrMail(prospect, allowed)
  }

  const planMap = planChannelByDay(campaign)
  const primary = (planMap && planMap[day]) || PLAYBOOK_CHANNEL_BY_DAY[day]
  if (!primary) {
    return channelDay1(allowed)
  }
  if (channelAvailable(prospect, primary, allowed)) {
    return primary
  }
  return fallbackChannel(primary, prospect, allowed)
}

export function channelLabel(ch) {
  const key = String(ch || '').toLowerCase()
  return CHANNEL_LABELS[key] || ch || '—'
}

export const SEQUENCE_GROUP_LABEL = {
  contactado: 'Contactados',
  proximo_follow_up: 'Próximos Follow-up',
  follow_ups: 'Follow-ups',
  postergado: 'Postergados',
  encajonado: 'Encajonados',
  descanso: 'Descanso',
  reuniones: 'Reuniones',
}

export function sequenceGroupLabel(group) {
  const g = String(group || 'contactado').toLowerCase()
  return SEQUENCE_GROUP_LABEL[g] || g
}

export function sequenceStateLabel(state) {
  const s = String(state || '').toLowerCase()
  if (s === 'link_enviado') {
    return 'Link de agenda enviado'
  }
  if (s === 'agendado') {
    return 'Reunión agendada'
  }
  if (s === 'con_respuesta') {
    return 'Con respuesta'
  }
  if (s === 'sin_respuesta') {
    return 'Sin respuesta'
  }
  if (!s) {
    return '—'
  }
  return s.replace(/_/g, ' ')
}

export function lastCompletedMilestone(prospect) {
  const fired = parseSequenceFired(prospect?.sequence_fired_milestones)
  if (!fired.length) {
    return 0
  }
  return Math.max(...fired)
}

/**
 * Resumen operativo para tablas / outreach (sin llamadas API).
 */
export function prospectNextMilestoneSummary(prospect, campaign) {
  const milestones = campaignMilestones(campaign)
  const g = String(prospect.sequence_group || '').toLowerCase()
  if (g === 'reuniones') {
    return {
      kind: 'reuniones',
      currentDay: sequenceCalendarDayIndex(prospect.sequence_started_at),
      line: 'Reuniones — evento real en Google Calendar; outreach automático en pausa.',
      shortWait: 'Agendado',
      channelLabel: '—',
    }
  }
  if (g === 'postergado') {
    return {
      kind: 'postergado',
      currentDay: sequenceCalendarDayIndex(prospect.sequence_started_at),
      line: 'En Postergados — el prospecto pidió retomar más adelante; Nexus reactiva en la fecha acordada.',
      shortWait: 'Postergado',
      channelLabel: '—',
    }
  }
  if (g === 'encajonado') {
    return {
      kind: 'encajonado',
      currentDay: sequenceCalendarDayIndex(prospect.sequence_started_at),
      line: 'En Encajonados — reactivá para volver a la secuencia.',
      shortWait: 'Encajonado',
      channelLabel: '—',
    }
  }
  if (prospect.sequence_paused) {
    return {
      kind: 'paused',
      currentDay: sequenceCalendarDayIndex(prospect.sequence_started_at),
      line: 'Secuencia en pausa — Nexus retoma al reanudar outreach.',
      shortWait: 'Pausado',
      channelLabel: '—',
    }
  }
  if (!prospect.sequence_started_at) {
    return {
      kind: 'idle',
      currentDay: 0,
      line: 'Sin secuencia anclada — inicia outreach para día 1.',
      shortWait: '—',
      channelLabel: '—',
    }
  }
  const fired = new Set(parseSequenceFired(prospect.sequence_fired_milestones))
  const calendarDay = sequenceCalendarDayIndex(prospect.sequence_started_at)
  const lastCompletedDay = lastCompletedMilestone(prospect)
  const pending = milestones.filter((m) => !fired.has(m))
  if (!pending.length) {
    return {
      kind: 'complete',
      currentDay: calendarDay,
      lastCompletedDay,
      line: 'Hitos de toque completados — descanso o seguimiento manual.',
      shortWait: 'Listo',
      channelLabel: '—',
    }
  }
  const next = pending[0]
  const ch = milestoneChannel(next, prospect, campaign)
  const chLabel = channelLabel(ch)
  const daysUntil = Math.max(0, next - calendarDay)
  const waitPhrase =
    daysUntil === 0
      ? 'pendiente en el próximo ciclo automático'
      : `en ${daysUntil} día${daysUntil === 1 ? '' : 's'}`
  return {
    kind: 'active',
    currentDay: calendarDay,
    lastCompletedDay,
    nextDay: next,
    channel: ch,
    channelLabel: chLabel,
    line: `Próximo toque ${waitPhrase} (${chLabel}) · día ${next}.`,
    shortWait: daysUntil === 0 ? `Hoy · ${chLabel}` : `${daysUntil}d · ${chLabel}`,
  }
}

export function milestoneCompletionCounts(prospects, campaign = null) {
  const milestones = campaignMilestones(campaign)
  const counts = Object.fromEntries(milestones.map((d) => [d, 0]))
  const list = Array.isArray(prospects) ? prospects : []
  for (const p of list) {
    if (!p.sequence_started_at) {
      continue
    }
    const fired = parseSequenceFired(p.sequence_fired_milestones)
    const set = new Set(fired)
    for (const m of milestones) {
      if (set.has(m)) {
        counts[m] += 1
      }
    }
  }
  const active = list.filter((p) => p.sequence_started_at).length
  return { counts, activeWithSequence: active }
}

export function milestoneShortLabel(day, campaign = null) {
  const d = normalizeMilestoneDay(day)
  if (d === REACTIVATION_DAY) {
    return `D${d} · follow-up`
  }
  const planMap = planChannelByDay(campaign)
  const ch = (planMap && planMap[d]) || PLAYBOOK_CHANNEL_BY_DAY[d]
  if (!ch) {
    return `D${d}`
  }
  return `D${d} · ${channelLabel(ch)}`
}

/** Canal visual del hito (para colores en UI). */
export function milestoneChannelKey(day, campaign = null) {
  const d = normalizeMilestoneDay(day)
  if (d === REACTIVATION_DAY) {
    const fuCh = String(campaign?.sequence_plan?.follow_up?.channel || 'auto').toLowerCase()
    if (fuCh === 'email' || fuCh === 'linkedin' || fuCh === 'whatsapp' || fuCh === 'call') return fuCh
    return 'followup'
  }
  const planMap = planChannelByDay(campaign)
  return (planMap && planMap[d]) || PLAYBOOK_CHANNEL_BY_DAY[d] || 'email'
}

/**
 * Estilos del chip de día según canal.
 * WhatsApp verde · LinkedIn azul · Email naranja · Llamada violeta · follow-up violeta suave.
 */
export function milestoneChannelChipClass(day, campaign = null, { completed = false } = {}) {
  const ch = milestoneChannelKey(day, campaign)
  const base = 'flex min-w-[3.25rem] flex-col items-center rounded-lg border px-2 py-2 text-center sm:min-w-[3.75rem]'
  if (ch === 'whatsapp') {
    return completed
      ? `${base} border-emerald-300 bg-emerald-50 text-emerald-950 shadow-sm shadow-emerald-900/5`
      : `${base} border-emerald-200/90 bg-emerald-50/70 text-emerald-900`
  }
  if (ch === 'linkedin') {
    return completed
      ? `${base} border-sky-300 bg-sky-50 text-sky-950 shadow-sm shadow-sky-900/5`
      : `${base} border-sky-200/90 bg-sky-50/70 text-sky-900`
  }
  if (ch === 'email') {
    return completed
      ? `${base} border-orange-300 bg-orange-50 text-orange-950 shadow-sm shadow-orange-900/5`
      : `${base} border-orange-200/90 bg-orange-50/70 text-orange-900`
  }
  if (ch === 'call') {
    return completed
      ? `${base} border-violet-400 bg-violet-50 text-violet-950 shadow-sm shadow-violet-900/5`
      : `${base} border-violet-300/90 bg-violet-50/70 text-violet-900`
  }
  // follow-up / desconocido
  return completed
    ? `${base} border-violet-300 bg-violet-50 text-violet-950 shadow-sm shadow-violet-900/5`
    : `${base} border-violet-200/90 bg-violet-50/70 text-violet-900`
}
