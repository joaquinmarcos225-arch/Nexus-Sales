/** Orden efectivo de prioridad (coincide con backend). */
export const CHANNEL_ORDER = ['linkedin', 'email', 'whatsapp']

export const CHANNEL_LABELS = {
  linkedin: 'LinkedIn',
  email: 'Email',
  whatsapp: 'WhatsApp',
}

export const DEFAULT_ALLOWED_CHANNELS = [...CHANNEL_ORDER]

/** A partir de cualquier subset, devuelve lista ordenada por prioridad. */
export function orderChannels(selected) {
  const set = new Set(
    Array.isArray(selected) ? selected.map((c) => String(c).toLowerCase()) : [],
  )
  return CHANNEL_ORDER.filter((c) => set.has(c))
}

export function formatChannelsSummary(channels) {
  const ch = orderChannels(channels)
  if (!ch.length) {
    return 'Ninguno'
  }
  return ch.map((id) => CHANNEL_LABELS[id] ?? id).join(' → ')
}
