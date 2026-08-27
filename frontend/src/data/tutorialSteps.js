/**
 * Pasos del tour guiado Nexus — targets vía data-tour en el DOM.
 */

/**
 * @typedef {{ id: string, target: string, route?: string, title: string, body: string }} TutorialStep
 */

/** @returns {TutorialStep[]} */
export function tutorialStepsForUser(_user) {
  return [
    {
      id: 'nav-consola',
      target: '[data-tour="nav-consola"]',
      route: '/dashboard',
      title: 'Consola del día',
      body: 'Acá ves lo urgente: LinkedIn listos, follow-ups, tareas de outreach y reuniones pendientes.',
    },
    {
      id: 'sdr-pillars',
      target: '[data-tour="sdr-pillars"]',
      route: '/dashboard',
      title: 'Accesos rápidos',
      body: 'Buscá leads, revisá outreach, reuniones o entrá directo a una campaña activa.',
    },
    {
      id: 'nav-campanas',
      target: '[data-tour="nav-campanas"]',
      route: '/campanas',
      title: 'Campañas',
      body: 'Creá campañas, activá el outreach automático y seguí el cupo de prospecciones (meta vs importados).',
    },
    {
      id: 'nav-equipo',
      target: '[data-tour="nav-equipo"]',
      route: '/equipo',
      title: 'Equipo',
      body: 'Métricas por SDR/AE: mensajes, respuestas, tasas y última actividad.',
    },
    {
      id: 'nav-creditos',
      target: '[data-tour="nav-creditos"]',
      route: '/creditos',
      title: 'Créditos',
      body: 'Los créditos limitan cuántos prospectos podés importar y activar en campañas.',
    },
    {
      id: 'nav-config',
      target: '[data-tour="nav-config"]',
      route: '/configuracion/integraciones',
      title: 'Configuración',
      body: 'Conectá Gmail, Google Calendar y WhatsApp para que Nexus ejecute secuencias y respuestas reales.',
    },
  ]
}

export const TUTORIAL_STORAGE_KEY = 'nexus_tutorial_v1_done'

export function tourIdFromNavIcon(icon) {
  const map = {
    resumen: 'nav-consola',
    campanas: 'nav-campanas',
    equipo: 'nav-equipo',
    creditos: 'nav-creditos',
    config: 'nav-config',
    productos: 'nav-productos',
  }
  return map[icon] || null
}
