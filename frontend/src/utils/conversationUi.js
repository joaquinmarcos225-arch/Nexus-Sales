export const CONVERSATION_STATE_LABELS = {
  sin_conversacion: 'Sin conversación',
  conversacion_automatica_activa: 'Conversación automática activa',
  esperando_respuesta: 'Esperando respuesta del prospecto',
  reunion_conseguida: 'Reunión conseguida',
  derivado_sdr: 'Derivado a SDR',
  no_interesado: 'No interesado',
}

export function conversationStateLabel(state) {
  const key = (state || 'sin_conversacion').toLowerCase()
  return CONVERSATION_STATE_LABELS[key] || key.replace(/_/g, ' ')
}

export function conversationStateBadgeClass(state) {
  const key = (state || '').toLowerCase()
  const map = {
    conversacion_automatica_activa: 'bg-red-50 text-red-800 ring-red-600/20',
    esperando_respuesta: 'bg-zinc-100 text-zinc-800 ring-zinc-500/20',
    reunion_conseguida: 'bg-zinc-900 text-white ring-zinc-900/30',
    derivado_sdr: 'bg-zinc-100 text-zinc-800 ring-zinc-500/20',
    no_interesado: 'bg-red-50 text-red-800 ring-red-600/20',
    sin_conversacion: 'bg-zinc-100 text-zinc-600 ring-zinc-500/20',
  }
  return map[key] || map.sin_conversacion
}

export function formatConfidence(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return '—'
  }
  return `${Math.round(Number(value) * 100)}%`
}

export function stripAutoReplyMarker(text) {
  const body = (text || '').trim()
  if (body.startsWith('[auto-reply:simulate:')) {
    const parts = body.split('\n\n')
    return parts.length > 1 ? parts.slice(1).join('\n\n').trim() : body
  }
  if (body.startsWith('[auto-reply:gmail:')) {
    const parts = body.split('\n\n')
    return parts.length > 1 ? parts.slice(1).join('\n\n').trim() : body
  }
  return body
}
