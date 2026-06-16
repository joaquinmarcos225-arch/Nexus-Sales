import { CHANNEL_LABELS, CHANNEL_ORDER, orderChannels } from './campaignChannels.js'

export const SEQUENCE_MILESTONES = [1, 4, 7, 10, 14, 18, 21, 42]

export function parseSequenceFired(raw) {
  if (Array.isArray(raw)) {
    return raw.map((x) => parseInt(String(x), 10)).filter((n) => Number.isFinite(n))
  }
  try {
    const arr = JSON.parse(String(raw || '[]'))
    return Array.isArray(arr)
      ? arr.map((x) => parseInt(String(x), 10)).filter((n) => Number.isFinite(n))
      : []
  } catch {
    return []
  }
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

function channelDay4(p, allowed) {
  if ((p.linkedin_url || '').trim() && allowed.includes('linkedin')) {
    return 'linkedin'
  }
  if ((p.phone || '').trim() && allowed.includes('whatsapp')) {
    return 'whatsapp'
  }
  if (allowed.includes('email')) {
    return 'email'
  }
  return allowed[0] || 'email'
}

function channelWaOrMail(p, allowed) {
  if ((p.phone || '').trim() && allowed.includes('whatsapp')) {
    return 'whatsapp'
  }
  if (allowed.includes('email')) {
    return 'email'
  }
  return allowed[0] || 'email'
}

export function milestoneChannel(m, prospect, campaign) {
  const allowed = orderedAllowedChannels(campaign)
  if (m === 1) {
    return channelDay1(allowed)
  }
  if (m === 4) {
    return channelDay4(prospect, allowed)
  }
  if (m === 7 || m === 14) {
    return channelWaOrMail(prospect, allowed)
  }
  if (m === 10 || m === 18) {
    return (prospect.linkedin_url || '').trim() && allowed.includes('linkedin') ? 'linkedin' : 'email'
  }
  if (m === 21) {
    return 'email'
  }
  if (m === 42) {
    return channelWaOrMail(prospect, allowed)
  }
  return 'email'
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

/**
 * Resumen operativo para tablas / outreach (sin llamadas API).
 */
export function prospectNextMilestoneSummary(prospect, campaign) {
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
  const day = sequenceCalendarDayIndex(prospect.sequence_started_at)
  const pending = SEQUENCE_MILESTONES.filter((m) => !fired.has(m))
  if (!pending.length) {
    return {
      kind: 'complete',
      currentDay: day,
      line: 'Hitos de toque completados — descanso o seguimiento manual.',
      shortWait: 'Listo',
      channelLabel: '—',
    }
  }
  const next = pending[0]
  const ch = milestoneChannel(next, prospect, campaign)
  const chLabel = channelLabel(ch)
  const daysUntil = Math.max(0, next - day)
  const waitPhrase =
    daysUntil === 0
      ? 'pendiente en el próximo ciclo automático'
      : `en ${daysUntil} día${daysUntil === 1 ? '' : 's'}`
  return {
    kind: 'active',
    currentDay: day,
    nextDay: next,
    channel: ch,
    channelLabel: chLabel,
    line: `Próximo follow-up ${waitPhrase} (${chLabel}) · hito día ${next}.`,
    shortWait: daysUntil === 0 ? `Hoy · ${chLabel}` : `${daysUntil}d · ${chLabel}`,
  }
}

export function milestoneCompletionCounts(prospects) {
  const counts = Object.fromEntries(SEQUENCE_MILESTONES.map((d) => [d, 0]))
  const list = Array.isArray(prospects) ? prospects : []
  for (const p of list) {
    if (!p.sequence_started_at) {
      continue
    }
    const fired = parseSequenceFired(p.sequence_fired_milestones)
    const set = new Set(fired)
    for (const m of SEQUENCE_MILESTONES) {
      if (set.has(m)) {
        counts[m] += 1
      }
    }
  }
  const active = list.filter((p) => p.sequence_started_at).length
  return { counts, activeWithSequence: active }
}
