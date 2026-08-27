/** Patrones de URL LinkedIn de simulación / demo (no abrir en asistido). */
const DEMO_PATH_RE = [
  /\/in\/demo[-_]/i,
  /\/in\/test[-_]/i,
  /\/in\/fake[-_]/i,
  /\/in\/mock[-_]/i,
  /\/in\/sample[-_]/i,
  /\/in\/example/i,
  /nexus-sales\.local/i,
]

/**
 * URL normalizada para abrir LinkedIn, o null si no es usable.
 * Limpia query (?skipRedirect=true, etc.) que rompe el CTA Mensaje.
 */
export function linkedInOpenUrl(raw) {
  const url = (raw || '').trim()
  if (!url) {
    return null
  }
  try {
    const parsed = new URL(url.startsWith('http') ? url : `https://${url}`)
    if (parsed.hostname.toLowerCase().includes('linkedin.com')) {
      let path = parsed.pathname || '/'
      // Decodificar slug (%C3%AD → í) y normalizar trailing slash.
      try {
        path = decodeURIComponent(path)
      } catch {
        /* keep */
      }
      path = path.replace(/\/+$/, '')
      if (path.startsWith('/in/') || path.startsWith('/sales/')) {
        return `https://www.linkedin.com${path}/`
      }
    }
    return parsed.toString()
  } catch {
    return null
  }
}

/**
 * URL de invitación Contactar (legacy). Preferir abrir solo el perfil.
 * Ej: /preload/custom-invite/?vanityName=valeriaaguerri
 */
export function linkedInConnectInviteUrl(raw) {
  const profile = linkedInOpenUrl(raw)
  if (!profile) return null
  try {
    const path = new URL(profile).pathname.toLowerCase()
    if (!path.startsWith('/in/')) return null
    const slug = decodeURIComponent(path.replace(/^\/in\//, '').split('/')[0] || '').trim()
    if (!slug) return null
    return `https://www.linkedin.com/preload/custom-invite/?vanityName=${encodeURIComponent(slug)}`
  } catch {
    return null
  }
}

/**
 * URL del perfil (fallback manual). El chat directo + pegado lo hace la extensión Chrome.
 */
export function linkedInMessagingUrl(raw) {
  return linkedInOpenUrl(raw)
}

/**
 * URL de chat LinkedIn a partir del URN interno (fsd_profile).
 * Es el mismo formato que genera el botón «Mensaje» del perfil.
 */
export function linkedInComposeUrlFromUrn(rawUrn) {
  const value = String(rawUrn || '').trim()
  if (!value) return null
  const idMatch = value.match(/urn:li:fsd_profile:([A-Za-z0-9_-]+)/i)
  const id = idMatch ? idMatch[1] : /^[A-Za-z0-9_-]{10,}$/.test(value) ? value : null
  if (!id) return null
  const encoded = encodeURIComponent(`urn:li:fsd_profile:${id}`)
  return (
    `https://www.linkedin.com/messaging/compose/` +
    `?profileUrn=${encoded}` +
    `&recipient=${encodeURIComponent(id)}` +
    `&screenContext=NON_SELF_PROFILE_VIEW` +
    `&interop=msgOverlay`
  )
}

/**
 * URL a abrir para enviar mensaje: compose si hay URN; si no, perfil.
 */
export function linkedInMessageOpenUrl({ linkedinUrl, linkedinProfileUrn, composeUrl }) {
  if (composeUrl && String(composeUrl).includes('/messaging/compose')) {
    return String(composeUrl).trim()
  }
  const fromUrn = linkedInComposeUrlFromUrn(linkedinProfileUrn)
  if (fromUrn) return fromUrn
  return linkedInOpenUrl(linkedinUrl)
}

/**
 * Perfil LinkedIn real del prospecto (excluye demos de simulación local).
 */
export function hasRealLinkedInUrl(raw) {
  const normalized = linkedInOpenUrl(raw)
  if (!normalized) {
    return false
  }
  try {
    const u = new URL(normalized)
    const host = u.hostname.toLowerCase()
    if (!host.includes('linkedin.com')) {
      return false
    }
    const path = u.pathname.toLowerCase()
    if (!path.startsWith('/in/') && !path.startsWith('/sales/')) {
      return false
    }
    if (DEMO_PATH_RE.some((re) => re.test(path) || re.test(normalized))) {
      return false
    }
    const slug = path.replace(/^\/in\//, '').split('/')[0]
    if (!slug || slug.length < 2) {
      return false
    }
    return true
  } catch {
    return false
  }
}

export function linkedInUrlLabel(raw) {
  return hasRealLinkedInUrl(raw) ? 'LinkedIn' : 'Sin LinkedIn configurado'
}

const ASSIST_STATUS_LABELS = {
  suggested: 'Pendiente',
  prepared: 'Mensaje listo',
  opened: 'Confirmar envío',
  sent: 'Enviado',
  none: '—',
}

const ASSIST_STATUS_STYLES = {
  suggested: 'bg-zinc-100 text-zinc-700 ring-zinc-200/80',
  prepared: 'bg-zinc-50 text-zinc-900 ring-zinc-200/80',
  opened: 'bg-zinc-50 text-zinc-950 ring-zinc-200/80',
  sent: 'bg-red-50 text-red-900 ring-red-200/80',
}

const PRIORITY_STYLES = {
  alta: 'bg-red-50 text-red-900 ring-red-200/70',
  media: 'bg-zinc-50 text-zinc-700 ring-zinc-200/70',
  baja: 'bg-zinc-50/80 text-zinc-500 ring-zinc-200/60',
}

/** Badge grande para Responder / alta prioridad. */
const PRIORITY_BADGE_STYLES = {
  alta: 'bg-red-600 text-white ring-2 ring-red-300 shadow-sm shadow-red-200/80',
  media: 'bg-zinc-700 text-white ring-1 ring-zinc-400',
  baja: 'bg-zinc-200 text-zinc-700 ring-1 ring-zinc-300',
}

export function linkedInAssistStatusLabel(status) {
  const key = (status || '').toLowerCase()
  return ASSIST_STATUS_LABELS[key] || ASSIST_STATUS_LABELS.suggested
}

export function linkedInAssistStatusClass(status) {
  const key = (status || '').toLowerCase()
  return ASSIST_STATUS_STYLES[key] || ASSIST_STATUS_STYLES.suggested
}

export function linkedInPriorityClass(priority) {
  const key = (priority || 'media').toLowerCase()
  return PRIORITY_STYLES[key] || PRIORITY_STYLES.media
}

export function linkedInPriorityLabel(priority) {
  const key = (priority || 'media').toLowerCase()
  if (key === 'alta') return 'Alta prioridad'
  if (key === 'baja') return 'Baja prioridad'
  return 'Prioridad media'
}

export function linkedInPriorityBadgeClass(priority) {
  const key = (priority || 'media').toLowerCase()
  return PRIORITY_BADGE_STYLES[key] || PRIORITY_BADGE_STYLES.media
}

export async function copyTextToClipboard(text) {
  const value = (text || '').trim()
  if (!value) {
    return false
  }
  try {
    await navigator.clipboard.writeText(value)
    return true
  } catch {
    return false
  }
}
