export const OWNERSHIP_STATUS_LABELS = {
  libre: 'Libre',
  tomado: 'Tomado',
  en_secuencia: 'En secuencia',
  secuencia_finalizada: 'Finalizado',
  liberado: 'Liberado',
}

/** Estados en los que un SDR/Manager puede tomar el prospecto. */
export const CLAIMABLE_OWNERSHIP_STATUSES = new Set(['libre', 'liberado'])

export function ownershipStatusLabel(status) {
  const key = String(status || 'libre').toLowerCase()
  return OWNERSHIP_STATUS_LABELS[key] || status || '—'
}

export function ownershipStatusBadgeClass(status) {
  const key = String(status || 'libre').toLowerCase()
  const map = {
    libre: 'bg-red-50 text-red-800 ring-red-600/20',
    tomado: 'bg-zinc-100 text-zinc-800 ring-zinc-500/20',
    en_secuencia: 'bg-zinc-900 text-white ring-zinc-900/25',
    secuencia_finalizada: 'bg-zinc-100 text-zinc-700 ring-zinc-500/20',
    liberado: 'bg-zinc-100 text-zinc-700 ring-zinc-500/20',
  }
  return map[key] || 'bg-zinc-100 text-zinc-700 ring-zinc-500/20'
}

export function parseApiDateTime(iso) {
  if (!iso) {
    return null
  }
  let s = String(iso).trim()
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s) && !/(Z|[+-]\d{2}:\d{2})$/.test(s)) {
    s = `${s}Z`
  }
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? null : d
}

export function fmtDateTime(iso, timezone) {
  const d = parseApiDateTime(iso)
  if (!d) {
    return '—'
  }
  try {
    const opts = { dateStyle: 'short', timeStyle: 'short' }
    if (timezone) {
      opts.timeZone = timezone
    }
    return d.toLocaleString('es-AR', opts)
  } catch {
    return '—'
  }
}

export function fmtDate(iso) {
  if (!iso) {
    return '—'
  }
  try {
    return new Date(iso).toLocaleDateString('es-AR', { dateStyle: 'short' })
  } catch {
    return '—'
  }
}

export function fmtTime(iso) {
  if (!iso) {
    return '—'
  }
  try {
    return new Date(iso).toLocaleTimeString('es-AR', { timeStyle: 'short' })
  } catch {
    return '—'
  }
}

export function ownerDisplayLabel(prospect) {
  if (prospect?.is_own_prospect) {
    return 'Yo'
  }
  return prospect?.owner_name || '—'
}

export function formatLastSequence(prospect) {
  if (prospect.last_sequence_label) {
    return prospect.last_sequence_label
  }
  if (prospect.sequence_completed_at) {
    return 'Finalizada'
  }
  return '—'
}
