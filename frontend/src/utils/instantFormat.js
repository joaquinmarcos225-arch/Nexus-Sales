/**
 * Normaliza instantes del API (UTC / offset) y muestra en la zona horaria local del navegador.
 * Si el string viene sin zona, se asume UTC (común con SQLite + serialización naive).
 */
export function parseApiInstant(iso) {
  if (iso == null) {
    return null
  }
  let s = String(iso).trim()
  if (!s) {
    return null
  }
  if (/^\d{4}-\d{2}-\d{2}[ T]\d/.test(s)) {
    s = s.replace(' ', 'T')
  }
  const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  if (!hasZone) {
    s = `${s}Z`
  }
  const ms = Date.parse(s)
  if (!Number.isFinite(ms)) {
    return null
  }
  return new Date(ms)
}

export function formatLocalDateTime(iso, locale = 'es-AR') {
  const d = parseApiInstant(iso)
  if (!d) {
    return '—'
  }
  try {
    return d.toLocaleString(locale, {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return d.toLocaleString(locale)
  }
}
