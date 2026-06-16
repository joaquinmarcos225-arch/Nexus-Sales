/** Alineado con backend `pipeline_sync.KANBAN_COLUMNS` (etapas comerciales). */
export const KANBAN_COLUMNS = [
  { id: 'nuevo', label: 'Nuevo', color: '#64748b', stages: ['nuevo'] },
  { id: 'contactado', label: 'Contactado', color: '#3b82f6', stages: ['contactado'] },
  { id: 'respondio', label: 'Respondió', color: '#8b5cf6', stages: ['respondio'] },
  { id: 'interesado', label: 'Interesado', color: '#a855f7', stages: ['interesado'] },
  { id: 'reunion', label: 'Reunión', color: '#0ea5e9', stages: ['reunion_agendada'] },
  {
    id: 'negociacion',
    label: 'Negociación',
    color: '#f59e0b',
    stages: ['negociacion', 'propuesta_enviada'],
  },
  { id: 'ganado', label: 'Ganado', color: '#22c55e', stages: ['cerrado_ganado'] },
  { id: 'perdido', label: 'Perdido', color: '#ef4444', stages: ['cerrado_perdido'] },
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
