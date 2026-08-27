export function formatUsd(amount, fractionDigits = 0) {
  return new Intl.NumberFormat('es-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(Number(amount) || 0)
}

/** Créditos de contacto (no dinero). */
export function formatContactCredits(amount) {
  const n = Number(amount) || 0
  const label = n === 1 ? 'crédito' : 'créditos'
  return `${n.toLocaleString('es-AR')} ${label}`
}
