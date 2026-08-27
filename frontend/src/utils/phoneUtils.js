/** Teléfonos/email parcialmente ocultos de Prospeo (ej. +54 9 342 6**-****). */
export function isMaskedContact(value) {
  const s = String(value || '').trim()
  if (!s) return false
  if (s.includes('*') || s.includes('#')) return true
  if (/x{2,}/i.test(s)) return true
  return false
}

/** Número usable para WhatsApp Web (completo, sin enmascarar). */
export function hasUsableWhatsApp(phone, whatsapp) {
  const raw = String(whatsapp || phone || '').trim()
  if (!raw || isMaskedContact(raw)) return false
  const digits = raw.replace(/\D/g, '')
  return digits.length >= 8
}
