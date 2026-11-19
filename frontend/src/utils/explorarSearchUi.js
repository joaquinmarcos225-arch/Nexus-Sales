/** Firma estable de filtros: si cambia y hay resultados viejos, se invalidan. */
export function filtersFingerprint(q) {
  const normList = (arr) =>
    (Array.isArray(arr) ? arr : [])
      .map((x) => String(x || '').trim())
      .filter(Boolean)
      .sort()
      .join('|')
  return [
    String(q.names || '').trim().toLowerCase(),
    String(q.titles || '').trim().toLowerCase(),
    String(q.location || '').trim().toLowerCase(),
    String(q.companyHq || '').trim().toLowerCase(),
    String(q.companies || '').trim().toLowerCase(),
    String(q.lookalikeUrls || '').trim().toLowerCase(),
    normList(q.industries),
    normList(q.headcounts),
    normList(q.seniorities),
    normList(q.departments),
  ].join('::')
}

/**
 * Total legible en la grilla (sin mentir el pozo ICP).
 * @returns {{ total: number, dishonest: boolean, label: 'pending' | 'found' }}
 */
export function searchResultsSummary({
  shown,
  pageSize = 25,
  rawTotal = 0,
  pages = 0,
  morePending = false,
  totalIsEstimate = false,
}) {
  const safeShown = Number(shown) || 0
  const safePageSize = Number(pageSize) || 25
  const safeRaw = Number(rawTotal) || 0
  const safePages = Number(pages) || 0
  // Solo achicar si el total miente de cerca (2 cards vs 23 en 1 pág).
  // No tapar ~300 ICP cuando la 1ª hoja trae 25–26 contactables.
  const dishonest =
    !morePending &&
    safePages <= 1 &&
    safeShown > 0 &&
    safeShown < safePageSize &&
    safeRaw > safeShown &&
    safeRaw <= safePageSize
  const total = dishonest ? safeShown : safeRaw
  const label = morePending && safeShown > 0 && safeShown < safePageSize ? 'pending' : 'found'
  return {
    total,
    dishonest,
    label,
    totalIsEstimate: Boolean(totalIsEstimate) && !dishonest,
    showPageHint: safeShown > 0 && safeShown < total && safePages > 1,
  }
}
