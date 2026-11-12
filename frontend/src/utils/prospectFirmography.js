/** Filas de firmografía para la ficha del prospecto (solo lectura; sin inventar datos). */

const OBSERVED_ORIGIN_KEYS = new Set(['prospeo', 'brave', 'web_search', 'company_extraction'])

export function normalizeDataOrigin(raw) {
  const key = String(raw || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '_')
  if (!key) {
    return { kind: 'unknown', label: 'No confirmado' }
  }
  if (key.includes('prospeo')) {
    return { kind: 'observed', label: 'Observado' }
  }
  if (OBSERVED_ORIGIN_KEYS.has(key) || key.includes('brave')) {
    return { kind: 'observed', label: 'Observado (web)' }
  }
  if (key === 'icp' || key === 'inherited' || key === 'campaign') {
    return { kind: 'inferred', label: 'Heredado de ICP' }
  }
  if (key === 'uncertain' || key === 'unknown' || key === 'historical') {
    return { kind: 'unknown', label: 'No confirmado' }
  }
  return { kind: 'unknown', label: 'No confirmado' }
}

export function provenanceTone(kind) {
  if (kind === 'observed') {
    return 'bg-emerald-50 text-emerald-900 ring-emerald-200'
  }
  if (kind === 'inferred') {
    return 'bg-amber-50 text-amber-950 ring-amber-200'
  }
  return 'bg-zinc-100 text-zinc-700 ring-zinc-200'
}

function clean(value) {
  const s = String(value ?? '').trim()
  return s || ''
}

export function parseIntentSignals(raw) {
  if (!raw) {
    return null
  }
  if (typeof raw === 'object') {
    return raw
  }
  try {
    const data = JSON.parse(String(raw))
    return data && typeof data === 'object' ? data : null
  } catch {
    return null
  }
}

/**
 * @param {Record<string, unknown> | null | undefined} prospect
 */
export function buildFirmographyRows(prospect) {
  if (!prospect) {
    return []
  }
  const rows = []

  const industry = clean(prospect.industry)
  if (industry) {
    const origin = normalizeDataOrigin(prospect.industry_origin)
    rows.push({
      id: 'industry',
      label: 'Industria',
      value: industry,
      kind: origin.kind,
      provenanceLabel: origin.label,
    })
  }

  const size = clean(prospect.company_size)
  if (size) {
    const origin = normalizeDataOrigin(prospect.size_origin)
    rows.push({
      id: 'company_size',
      label: 'Tamaño',
      value: size,
      kind: origin.kind,
      provenanceLabel: origin.label,
    })
  }

  const employees = prospect.employee_count
  if (employees != null && employees !== '' && !Number.isNaN(Number(employees))) {
    const origin = normalizeDataOrigin(prospect.size_origin)
    rows.push({
      id: 'employee_count',
      label: 'Empleados',
      value: String(employees),
      kind: origin.kind,
      provenanceLabel: origin.label,
    })
  }

  const website = clean(prospect.company_website) || clean(prospect.company_domain)
  if (website) {
    rows.push({
      id: 'website',
      label: 'Sitio web',
      value: website,
      kind: 'observed',
      provenanceLabel: 'Registrado en Nexus',
    })
  }

  return rows
}

export function firmographyScoreReason(prospect) {
  return clean(prospect?.score_reason)
}
