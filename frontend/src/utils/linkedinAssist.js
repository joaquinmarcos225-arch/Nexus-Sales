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
 */
export function linkedInOpenUrl(raw) {
  const url = (raw || '').trim()
  if (!url) {
    return null
  }
  try {
    const parsed = new URL(url.startsWith('http') ? url : `https://${url}`)
    return parsed.toString()
  } catch {
    return null
  }
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
  prepared: 'bg-sky-50 text-sky-900 ring-sky-200/80',
  opened: 'bg-amber-50 text-amber-950 ring-amber-200/80',
  sent: 'bg-emerald-50 text-emerald-900 ring-emerald-200/80',
}

const PRIORITY_STYLES = {
  alta: 'bg-rose-50 text-rose-900 ring-rose-200/70',
  media: 'bg-zinc-50 text-zinc-700 ring-zinc-200/70',
  baja: 'bg-zinc-50/80 text-zinc-500 ring-zinc-200/60',
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
