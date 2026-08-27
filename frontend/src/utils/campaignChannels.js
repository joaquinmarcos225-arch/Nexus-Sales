/** Orden de canales: preserva el orden en que el usuario los eligió / guardó. */
export const CHANNEL_ORDER = ['linkedin', 'email', 'whatsapp', 'call']

export const CHANNEL_LABELS = {
  linkedin: 'LinkedIn',
  email: 'Email',
  whatsapp: 'WhatsApp',
  call: 'Llamada',
}

/** Canales por defecto: LinkedIn → Email → WhatsApp → Llamada asistida. */
export const DEFAULT_ALLOWED_CHANNELS = ['linkedin', 'email', 'whatsapp', 'call']

/** Devuelve la lista en el orden dado (sin forzar LinkedIn→Email→WhatsApp). */
export function orderChannels(selected) {
  const out = []
  const seen = new Set()
  const allowed = new Set(CHANNEL_ORDER)
  for (const c of Array.isArray(selected) ? selected : []) {
    const lc = String(c).toLowerCase()
    if (allowed.has(lc) && !seen.has(lc)) {
      seen.add(lc)
      out.push(lc)
    }
  }
  return out
}

export function formatChannelsSummary(channels) {
  const ch = orderChannels(channels)
  if (!ch.length) {
    return 'Ninguno'
  }
  return ch.map((id) => CHANNEL_LABELS[id] ?? id).join(' → ')
}
