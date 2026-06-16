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
    libre: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
    tomado: 'bg-amber-50 text-amber-800 ring-amber-600/20',
    en_secuencia: 'bg-sky-50 text-sky-800 ring-sky-600/20',
    secuencia_finalizada: 'bg-violet-50 text-violet-800 ring-violet-600/20',
    liberado: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  }
  return map[key] || 'bg-slate-100 text-slate-700 ring-slate-500/20'
}

export function fmtDateTime(iso) {
  if (!iso) {
    return '—'
  }
  try {
    return new Date(iso).toLocaleString('es-AR', {
      dateStyle: 'short',
      timeStyle: 'short',
    })
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
