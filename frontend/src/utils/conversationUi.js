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
    conversacion_automatica_activa: 'bg-sky-50 text-sky-800 ring-sky-600/20',
    esperando_respuesta: 'bg-indigo-50 text-indigo-800 ring-indigo-600/20',
    reunion_conseguida: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
    derivado_sdr: 'bg-amber-50 text-amber-900 ring-amber-600/20',
    no_interesado: 'bg-red-50 text-red-800 ring-red-600/20',
    sin_conversacion: 'bg-slate-100 text-slate-600 ring-slate-500/20',
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
