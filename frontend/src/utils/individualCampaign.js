/** Contenedor fijo de secuencias individuales (no se elimina). */
export function isIndividualContainerCampaign(campaignOrName) {
  const name = String(
    typeof campaignOrName === 'string'
      ? campaignOrName
      : campaignOrName?.name || '',
  ).trim()
  return (
    name === 'Secuencias individuales' ||
    name.startsWith('Nexus · Secuencias individuales')
  )
}
