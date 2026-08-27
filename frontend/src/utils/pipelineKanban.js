/** Alineado con backend `pipeline_sync.KANBAN_COLUMNS` (etapas comerciales). */
export const KANBAN_COLUMNS = [
  { id: 'nuevo', label: 'Nuevo', color: '#71717a', stages: ['nuevo'] },
  { id: 'contactado', label: 'Contactado', color: '#a1a1aa', stages: ['contactado'] },
  { id: 'respondio', label: 'Respondió', color: '#f87171', stages: ['respondio'] },
  { id: 'interesado', label: 'Interesado', color: '#ef4444', stages: ['interesado'] },
  { id: 'reunion', label: 'Reunión', color: '#dc2626', stages: ['reunion_agendada'] },
  {
    id: 'negociacion',
    label: 'Negociación',
    color: '#991b1b',
    stages: ['negociacion', 'propuesta_enviada'],
  },
  { id: 'ganado', label: 'Ganado', color: '#18181b', stages: ['cerrado_ganado'] },
  { id: 'perdido', label: 'Perdido', color: '#7f1d1d', stages: ['cerrado_perdido'] },
]

export function stageForDroppedColumn(columnId) {
  const col = KANBAN_COLUMNS.find((c) => c.id === columnId)
  return col?.stages?.[0] ?? 'nuevo'
}

export function columnIdForPipelineStage(stage) {
  if (!stage) return 'nuevo'
  for (const c of KANBAN_COLUMNS) {
    if (c.stages.includes(stage)) return c.id
  }
  return 'nuevo'
}
