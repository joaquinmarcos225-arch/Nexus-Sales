/**
 * Confirmación antes de borrar una campaña.
 * Si hay actividad (running, autopilot, prospectos), el aviso es más fuerte.
 *
 * @param {{ name?: string, status?: string, autopilot_status?: string, automation_paused?: boolean }} campaign
 * @param {{ prospectsCount?: number }} [opts]
 * @returns {boolean}
 */
export function confirmDeleteCampaign(campaign, opts = {}) {
  const name = (campaign?.name || 'esta campaña').trim()
  const status = String(campaign?.status || '').toLowerCase()
  const autopilot = String(campaign?.autopilot_status || '').toLowerCase()
  const prospectsCount = Number(opts.prospectsCount) || 0

  const isActive =
    status === 'running' ||
    autopilot === 'running' ||
    (status === 'paused' && prospectsCount > 0) ||
    prospectsCount > 0

  if (isActive) {
    const bits = []
    if (status === 'running') bits.push('secuencia en curso')
    if (status === 'paused') bits.push('secuencia en pausa')
    if (autopilot === 'running') bits.push('autopilot activo')
    if (prospectsCount > 0) bits.push(`${prospectsCount} prospecto${prospectsCount === 1 ? '' : 's'}`)

    return window.confirm(
      `¿Seguro que querés eliminar «${name}»?\n\n` +
        `Tiene actividad: ${bits.join(', ')}.\n` +
        `Se borrarán prospectos, secuencias, mensajes y reuniones de esta campaña.\n` +
        `Los créditos de cupo no usados se liberan.\n\n` +
        `Esta acción no se puede deshacer.`,
    )
  }

  return window.confirm(
    `¿Eliminar la campaña «${name}»?\n\nEsta acción no se puede deshacer.`,
  )
}
