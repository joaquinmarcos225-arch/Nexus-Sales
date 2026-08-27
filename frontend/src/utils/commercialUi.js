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

/** Chips del pipeline CRM (orden visual). Tones: zinc | red | black. */
export const COMMERCIAL_PIPELINE_CHIPS = [
  { key: 'prospeccion', summaryKey: 'prospeccion', label: 'Prospección', tone: 'zinc' },
  { key: 'interesado', summaryKey: 'interesados', label: 'Interesados', tone: 'red' },
  {
    key: 'reunion_pendiente',
    summaryKey: 'reuniones_pendientes',
    label: 'Reuniones pendientes',
    tone: 'redSoft',
  },
  {
    key: 'reunion_agendada',
    summaryKey: 'reuniones_agendadas',
    label: 'Reuniones agendadas',
    tone: 'black',
  },
  { key: 'cliente', summaryKey: 'clientes', label: 'Clientes', tone: 'red' },
  { key: 'derivado', summaryKey: 'derivados', label: 'Derivados', tone: 'zinc' },
  { key: 'no_prioridad', summaryKey: 'no_prioridad', label: 'No prioridad', tone: 'zinc' },
  { key: 'no_interesado', summaryKey: 'no_interesados', label: 'No interesados', tone: 'red' },
]

export function commercialStateLabel(state) {
  const key = String(state || 'prospeccion').toLowerCase()
  return COMMERCIAL_STATE_LABELS[key] || state || '—'
}

export function commercialStateBadgeClass(state) {
  const key = String(state || 'prospeccion').toLowerCase()
  const map = {
    prospeccion: 'bg-zinc-100 text-zinc-700 ring-zinc-500/20',
    interesado: 'bg-red-50 text-red-800 ring-red-600/20',
    reunion_pendiente: 'bg-red-50/80 text-zinc-900 ring-red-400/25',
    reunion_agendada: 'bg-zinc-900 text-white ring-zinc-900/30',
    no_prioridad: 'bg-zinc-100 text-zinc-800 ring-zinc-500/20',
    derivado: 'bg-zinc-100 text-zinc-800 ring-zinc-500/20',
    no_interesado: 'bg-red-50 text-red-800 ring-red-600/20',
    cliente: 'bg-red-50 text-red-900 ring-red-600/20',
  }
  return map[key] || map.prospeccion
}

export function testingBadgeClass() {
  return 'bg-red-50 text-red-900 ring-red-600/25'
}

export function pipelineChipToneClass(tone, active = false) {
  const map = {
    zinc: active
      ? 'bg-zinc-800 text-white ring-zinc-800'
      : 'bg-zinc-50 text-zinc-800 ring-zinc-400/30 hover:bg-zinc-100',
    slate: active
      ? 'bg-zinc-800 text-white ring-zinc-800'
      : 'bg-zinc-50 text-zinc-800 ring-zinc-400/30 hover:bg-zinc-100',
    red: active
      ? 'bg-red-700 text-white ring-red-700'
      : 'bg-red-50 text-red-900 ring-red-600/20 hover:bg-red-100',
    redSoft: active
      ? 'bg-red-800 text-white ring-red-800'
      : 'bg-red-50/70 text-zinc-900 ring-red-400/25 hover:bg-red-50',
    black: active
      ? 'bg-black text-white ring-black'
      : 'bg-zinc-900 text-zinc-100 ring-zinc-800 hover:bg-zinc-800',
    // aliases legacy → paleta unificada
    emerald: null,
    indigo: null,
    violet: null,
    teal: null,
    orange: null,
    amber: null,
  }
  map.emerald = map.red
  map.indigo = map.redSoft
  map.violet = map.black
  map.teal = map.red
  map.orange = map.zinc
  map.amber = map.zinc
  return map[tone] || map.zinc
}
