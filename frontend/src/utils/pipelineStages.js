/** Etapas del pipeline comercial para vista manager. */

export const PIPELINE_STAGES = [
  { key: 'interesado', label: 'Interesados', tone: 'hot' },
  { key: 'reunion_agendada', label: 'Reunión agendada', tone: 'hot' },
  { key: 'reunion_pendiente', label: 'Reunión pendiente', tone: 'warm' },
  { key: 'respondio', label: 'Respondieron', tone: 'warm' },
  { key: 'en_secuencia', label: 'En secuencia', tone: 'neutral' },
  { key: 'prospeccion', label: 'Prospección', tone: 'muted' },
  { key: 'no_interesado', label: 'No interesado', tone: 'muted' },
  { key: 'no_prioridad', label: 'No prioridad', tone: 'muted' },
]

const COMMERCIAL_KEYS = new Set(PIPELINE_STAGES.map((s) => s.key))

/**
 * Normaliza etapa comercial + status legacy a una clave de pipeline.
 * @param {object} prospect
 */
export function pipelineStageKey(prospect) {
  const commercial = String(prospect?.commercial_state || '').trim().toLowerCase()
  if (commercial && COMMERCIAL_KEYS.has(commercial)) {
    return commercial
  }
  if (commercial && commercial !== 'prospeccion') {
    return commercial
  }
  const st = String(prospect?.status || '').trim().toLowerCase()
  if (st === 'replied' || st === 'interested' || (prospect?.last_inbound_at || '').trim()) {
    return 'respondio'
  }
  if (st === 'contacted' || st === 'meeting_booked') {
    return st === 'meeting_booked' ? 'reunion_agendada' : 'en_secuencia'
  }
  if (st === 'not_interested') {
    return 'no_interesado'
  }
  return 'prospeccion'
}

export function pipelineStageLabel(key) {
  return PIPELINE_STAGES.find((s) => s.key === key)?.label || key.replace(/_/g, ' ')
}

export function countByPipelineStage(rows) {
  const counts = Object.fromEntries(PIPELINE_STAGES.map((s) => [s.key, 0]))
  for (const row of rows || []) {
    const k = pipelineStageKey(row)
    counts[k] = (counts[k] || 0) + 1
  }
  return counts
}
