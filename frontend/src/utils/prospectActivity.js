/**
 * Estado operativo de un prospecto (lista de campaña / secuencia).
 * @param {object} p
 * @returns {{ code: string, label: string, tone: string, deadlineAt?: string|null, showCountdown?: boolean }}
 */
export function resolveProspectActivity(p) {
  if (!p || typeof p !== 'object') {
    return { code: 'unknown', label: '—', tone: 'muted' }
  }

  // Preferir campos del backend si vienen (label vacío = sin badge de estado).
  if (p.activity_label != null && String(p.activity_code || '') !== '') {
    const label = String(p.activity_label || '').trim()
    if (!label || p.activity_code === 'none') {
      // seguir con heurística local / null
    } else {
      return {
        code: p.activity_code || 'custom',
        label,
        tone: p.activity_tone || 'muted',
        deadlineAt: p.channel_enrich_deadline_at || p.activity_deadline_at || null,
        showCountdown:
          String(p.activity_code || '') === 'enriching' ||
          String(p.channel_enrich_status || '').toLowerCase() === 'searching',
      }
    }
  }

  const enrich = String(p.channel_enrich_status || '').toLowerCase()
  if (enrich === 'searching') {
    return {
      code: 'enriching',
      label: p.channel_enrich_message || 'Buscando información de canales…',
      tone: 'search',
      deadlineAt: p.channel_enrich_deadline_at || null,
      showCountdown: true,
    }
  }

  if (p.sequence_paused) {
    return { code: 'paused', label: 'Secuencia pausada', tone: 'muted' }
  }

  const status = String(p.status || '').toLowerCase()
  if (status === 'meeting_booked' || status === 'won') {
    return { code: 'meeting', label: 'Reunión agendada', tone: 'ok' }
  }
  if (status === 'not_interested') {
    return { code: 'closed', label: 'No interesado', tone: 'muted' }
  }

  if (!p.sequence_started_at) {
    return { code: 'idle', label: 'Guardado · esperando inicio', tone: 'muted' }
  }

  const conn = String(p.linkedin_connection_status || 'none').toLowerCase()
  if (['invite_pending', 'invite_sent', 'checking', 'check_queued', 'check_failed'].includes(conn)) {
    return {
      code: 'linkedin_connect',
      label:
        conn === 'check_failed'
          ? 'No se pudo verificar LinkedIn'
          : conn === 'checking' || conn === 'check_queued'
            ? 'Verificando si es contacto en LinkedIn'
            : 'Esperando conexión en LinkedIn',
      tone: 'wait',
    }
  }

  const liAssist = String(p.linkedin_assist_status || '').toLowerCase()
  const liSent = Boolean(p.linkedin_sdr_marked_sent_at)
  if (
    conn === 'connected' &&
    !liSent &&
    ['suggested', 'prepared', 'opened', 'queued'].includes(liAssist)
  ) {
    return { code: 'linkedin_message', label: 'Esperando envío de mensaje LinkedIn', tone: 'wait' }
  }

  const waAssist = String(p.whatsapp_assist_status || '').toLowerCase()
  if (['suggested', 'prepared', 'opened', 'queued'].includes(waAssist)) {
    return { code: 'whatsapp_message', label: 'Esperando envío de WhatsApp', tone: 'wait' }
  }

  const current = String(p.sequence_current_label || '').trim()
  const nextLbl = String(p.next_touch_label || '').trim()
  if (/gmail|email|correo/i.test(`${current} ${nextLbl}`)) {
    return {
      code: 'email',
      label: current || nextLbl || 'Enviando / pendiente Gmail',
      tone: 'active',
    }
  }
  if (current || nextLbl) {
    return { code: 'sequence', label: current || nextLbl, tone: 'active' }
  }

  const group = String(p.sequence_group || '').toLowerCase()
  if (group === 'encajonado') {
    return { code: 'boxed', label: 'Encajonado · esperando respuesta', tone: 'wait' }
  }
  if (group === 'postergado') {
    return { code: 'deferred', label: 'Postergado', tone: 'muted' }
  }

  // Nunca mostrar «Iniciando secuencia…».
  return null
}
