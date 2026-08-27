/**
 * Avisa a la extensión que deje de vigilar inbound de un prospecto
 * (pausa, handoff, eliminar de campaña).
 */
export function clearProspectExtensionWatch(prospectId) {
  const id = Number(prospectId) || 0
  if (!id) return
  try {
    if (typeof window.nexusClearProspectWatch === 'function') {
      window.nexusClearProspectWatch(id)
      return
    }
  } catch {
    /* ignore */
  }
  try {
    window.postMessage(
      { type: 'NEXUS_CLEAR_PROSPECT_WATCH', payload: { prospectId: id } },
      '*',
    )
  } catch {
    /* ignore */
  }
}
