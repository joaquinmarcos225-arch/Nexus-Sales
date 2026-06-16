export const COMMERCIAL_STATE_LABELS = {
  prospeccion: 'Prospección',
  interesado: 'Interesado',
  reunion_pendiente: 'Reunión pendiente',
  reunion_agendada: 'Reunión agendada',
  no_prioridad: 'No prioridad',
  derivado: 'Derivado',
  no_interesado: 'No interesado',
  cliente: 'Cliente',
}

/** Filtros del dropdown — alineados con estados comerciales del backend. */
export const COMMERCIAL_FILTER_OPTIONS = [
  { value: '', label: 'Todos (comercial)' },
  { value: 'prospeccion', label: 'Prospección' },
  { value: 'interesado', label: 'Interesados' },
  { value: 'reunion_pendiente', label: 'Reuniones pendientes' },
  { value: 'reunion_agendada', label: 'Reuniones agendadas' },
  { value: 'derivado', label: 'Derivados' },
  { value: 'no_prioridad', label: 'No prioridad' },
  { value: 'no_interesado', label: 'No interesados' },
  { value: 'cliente', label: 'Clientes' },
]

/** Chips del pipeline CRM (orden visual). */
export const COMMERCIAL_PIPELINE_CHIPS = [
  { key: 'prospeccion', summaryKey: 'prospeccion', label: 'Prospección', tone: 'slate' },
  { key: 'interesado', summaryKey: 'interesados', label: 'Interesados', tone: 'emerald' },
  {
    key: 'reunion_pendiente',
    summaryKey: 'reuniones_pendientes',
    label: 'Reuniones pendientes',
    tone: 'indigo',
  },
  {
    key: 'reunion_agendada',
    summaryKey: 'reuniones_agendadas',
    label: 'Reuniones agendadas',
    tone: 'violet',
  },
  { key: 'cliente', summaryKey: 'clientes', label: 'Clientes', tone: 'teal' },
  { key: 'derivado', summaryKey: 'derivados', label: 'Derivados', tone: 'orange' },
  { key: 'no_prioridad', summaryKey: 'no_prioridad', label: 'No prioridad', tone: 'amber' },
  { key: 'no_interesado', summaryKey: 'no_interesados', label: 'No interesados', tone: 'red' },
]

export function commercialStateLabel(state) {
  const key = String(state || 'prospeccion').toLowerCase()
  return COMMERCIAL_STATE_LABELS[key] || state || '—'
}

export function commercialStateBadgeClass(state) {
  const key = String(state || 'prospeccion').toLowerCase()
  const map = {
    prospeccion: 'bg-slate-100 text-slate-700 ring-slate-500/20',
    interesado: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
    reunion_pendiente: 'bg-indigo-50 text-indigo-800 ring-indigo-600/20',
    reunion_agendada: 'bg-violet-50 text-violet-800 ring-violet-600/20',
    no_prioridad: 'bg-amber-50 text-amber-900 ring-amber-600/20',
    derivado: 'bg-orange-50 text-orange-900 ring-orange-600/20',
    no_interesado: 'bg-red-50 text-red-800 ring-red-600/20',
    cliente: 'bg-teal-50 text-teal-900 ring-teal-600/20',
  }
  return map[key] || map.prospeccion
}

export function testingBadgeClass() {
  return 'bg-fuchsia-50 text-fuchsia-900 ring-fuchsia-600/25'
}

export function pipelineChipToneClass(tone, active = false) {
  const map = {
    slate: active
      ? 'bg-slate-800 text-white ring-slate-800'
      : 'bg-slate-50 text-slate-800 ring-slate-400/30 hover:bg-slate-100',
    emerald: active
      ? 'bg-emerald-700 text-white ring-emerald-700'
      : 'bg-emerald-50 text-emerald-900 ring-emerald-600/20 hover:bg-emerald-100',
    indigo: active
      ? 'bg-indigo-700 text-white ring-indigo-700'
      : 'bg-indigo-50 text-indigo-900 ring-indigo-600/20 hover:bg-indigo-100',
    violet: active
      ? 'bg-violet-700 text-white ring-violet-700'
      : 'bg-violet-50 text-violet-900 ring-violet-600/20 hover:bg-violet-100',
    teal: active
      ? 'bg-teal-700 text-white ring-teal-700'
      : 'bg-teal-50 text-teal-900 ring-teal-600/20 hover:bg-teal-100',
    orange: active
      ? 'bg-orange-700 text-white ring-orange-700'
      : 'bg-orange-50 text-orange-950 ring-orange-600/20 hover:bg-orange-100',
    amber: active
      ? 'bg-amber-700 text-white ring-amber-700'
      : 'bg-amber-50 text-amber-950 ring-amber-600/20 hover:bg-amber-100',
    red: active
      ? 'bg-red-700 text-white ring-red-700'
      : 'bg-red-50 text-red-900 ring-red-600/20 hover:bg-red-100',
  }
  return map[tone] || map.indigo
}
