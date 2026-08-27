const FALLBACK_TIMEZONES = [
  'America/Argentina/Buenos_Aires',
  'America/Santiago',
  'America/Mexico_City',
  'America/Bogota',
  'America/Lima',
  'America/Sao_Paulo',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Europe/Madrid',
  'Europe/London',
  'UTC',
]

/** Canonical IANA for Argentina — same offset nationwide (no provincial split). */
export const ARGENTINA_TIMEZONE = 'America/Argentina/Buenos_Aires'

/** Legacy / provincial IANA ids that share Argentina's single offset. */
const ARGENTINA_ALIAS_IDS = new Set([
  'America/Argentina/Buenos_Aires',
  'America/Argentina/Catamarca',
  'America/Argentina/ComodRivadavia',
  'America/Argentina/Cordoba',
  'America/Argentina/Jujuy',
  'America/Argentina/La_Rioja',
  'America/Argentina/Mendoza',
  'America/Argentina/Rio_Gallegos',
  'America/Argentina/Salta',
  'America/Argentina/San_Juan',
  'America/Argentina/San_Luis',
  'America/Argentina/Tucuman',
  'America/Argentina/Ushuaia',
  'America/Buenos_Aires',
  'America/Catamarca',
  'America/Cordoba',
  'America/Jujuy',
  'America/Mendoza',
  'America/Rosario',
])

/** Región (español) por primer segmento IANA tras el continente. */
const LOCATION_REGION = {
  Argentina: 'Argentina',
  Buenos_Aires: 'Argentina',
  Cordoba: 'Argentina',
  Mendoza: 'Argentina',
  Catamarca: 'Argentina',
  Jujuy: 'Argentina',
  Rosario: 'Argentina',
  Santiago: 'Chile',
  Mexico_City: 'México',
  Cancun: 'México',
  Tijuana: 'México',
  Bogota: 'Colombia',
  Lima: 'Perú',
  Sao_Paulo: 'Brasil',
  Rio_Branco: 'Brasil',
  Manaus: 'Brasil',
  New_York: 'Estados Unidos',
  Chicago: 'Estados Unidos',
  Denver: 'Estados Unidos',
  Los_Angeles: 'Estados Unidos',
  Phoenix: 'Estados Unidos',
  Anchorage: 'Estados Unidos',
  Honolulu: 'Estados Unidos',
  Toronto: 'Canadá',
  Vancouver: 'Canadá',
  Madrid: 'España',
  Barcelona: 'España',
  London: 'Reino Unido',
  Paris: 'Francia',
  Berlin: 'Alemania',
  Rome: 'Italia',
  Lisbon: 'Portugal',
  Amsterdam: 'Países Bajos',
  Brussels: 'Bélgica',
  Zurich: 'Suiza',
  Vienna: 'Austria',
  Warsaw: 'Polonia',
  Moscow: 'Rusia',
  Istanbul: 'Turquía',
  Dubai: 'Emiratos Árabes Unidos',
  Kolkata: 'India',
  Mumbai: 'India',
  Shanghai: 'China',
  Tokyo: 'Japón',
  Seoul: 'Corea del Sur',
  Singapore: 'Singapur',
  Sydney: 'Australia',
  Melbourne: 'Australia',
  Perth: 'Australia',
  Auckland: 'Nueva Zelanda',
  Johannesburg: 'Sudáfrica',
  Cairo: 'Egipto',
  Lagos: 'Nigeria',
  Montevideo: 'Uruguay',
  Asuncion: 'Paraguay',
  La_Paz: 'Bolivia',
  Caracas: 'Venezuela',
  Guayaquil: 'Ecuador',
  Costa_Rica: 'Costa Rica',
  Panama: 'Panamá',
  Guatemala: 'Guatemala',
  El_Salvador: 'El Salvador',
  Tegucigalpa: 'Honduras',
  Managua: 'Nicaragua',
  Santo_Domingo: 'República Dominicana',
  Puerto_Rico: 'Puerto Rico',
  Havana: 'Cuba',
  Jamaica: 'Jamaica',
  Barbados: 'Barbados',
  Dublin: 'Irlanda',
  Athens: 'Grecia',
  Stockholm: 'Suecia',
  Oslo: 'Noruega',
  Helsinki: 'Finlandia',
  Copenhagen: 'Dinamarca',
  Prague: 'República Checa',
  Budapest: 'Hungría',
  Bucharest: 'Rumania',
  Kiev: 'Ucrania',
  Jerusalem: 'Israel',
  Riyadh: 'Arabia Saudita',
  Bangkok: 'Tailandia',
  Jakarta: 'Indonesia',
  Manila: 'Filipinas',
  Hong_Kong: 'Hong Kong',
  Taipei: 'Taiwán',
}

/** Regiones que entran en búsqueda "latam" (sin Brasil). */
const LATAM_REGIONS = new Set([
  'Argentina',
  'Chile',
  'Colombia',
  'Perú',
  'Uruguay',
  'Paraguay',
  'Bolivia',
  'Ecuador',
  'Venezuela',
  'México',
  'Costa Rica',
  'Panamá',
  'Guatemala',
  'El Salvador',
  'Honduras',
  'Nicaragua',
  'República Dominicana',
  'Puerto Rico',
  'Cuba',
  'Jamaica',
  'Barbados',
])

/** Países con husos distintos reales (ciudad importa). Argentina NO: un solo huso. */
const MULTI_ZONE_REGIONS = new Set([
  'Estados Unidos',
  'Canadá',
  'Brasil',
  'México',
  'Australia',
  'Rusia',
  'Indonesia',
  'Reino Unido',
])

/** Colapsa alias provinciales de Argentina al IANA canónico. */
export function canonicalizeTimezoneId(timeZoneId) {
  const id = String(timeZoneId || '').trim()
  if (!id) return id
  const lower = id.toLowerCase()
  if (lower.startsWith('america/argentina/')) {
    return ARGENTINA_TIMEZONE
  }
  for (const alias of ARGENTINA_ALIAS_IDS) {
    if (alias.toLowerCase() === lower) {
      return ARGENTINA_TIMEZONE
    }
  }
  return id
}

function humanizeSegment(seg) {
  return String(seg || '')
    .replace(/_/g, ' ')
    .trim()
}

function inferRegionAndCity(timeZoneId) {
  if (timeZoneId === 'UTC') {
    return { region: 'UTC', city: '' }
  }
  const parts = timeZoneId.split('/')
  if (parts.length < 2) {
    return { region: timeZoneId, city: '' }
  }
  const continent = parts[0]
  const locParts = parts.slice(1)
  const head = locParts[0]
  const region = LOCATION_REGION[head] ?? humanizeSegment(head)
  // Argentina: nunca mostrar provincia (mismo huso en todo el país).
  if (region === 'Argentina' || head === 'Argentina') {
    return { region: 'Argentina', city: '' }
  }
  let city = ''
  if (locParts.length > 1) {
    city = humanizeSegment(locParts.slice(1).join(' '))
  } else if (MULTI_ZONE_REGIONS.has(region) && head !== region) {
    city = humanizeSegment(head)
  } else if (!LOCATION_REGION[head] && continent !== 'Etc') {
    city = humanizeSegment(head)
  }
  return { region, city }
}

function buildLabel(region, city, regionZoneCount) {
  if (region === 'UTC') return 'UTC'
  if (!city || regionZoneCount <= 1) return region
  return `${region} — ${city}`
}

function buildSearchText(region, city, id) {
  const parts = [region, city, id.replace(/_/g, ' ')]
  if (region === 'Brasil') {
    parts.push('brasil', 'brazil')
  } else if (region === 'Argentina') {
    parts.push(
      'argentina',
      'buenos aires',
      'córdoba',
      'cordoba',
      'mendoza',
      'latam',
      'américa latina',
      'america latina',
    )
  } else if (LATAM_REGIONS.has(region)) {
    parts.push('latam', 'latin america', 'américa latina', 'america latina')
  }
  return parts.filter(Boolean).join(' ').toLowerCase()
}

/** Todas las zonas IANA agrupadas por región, etiqueta simple (sin hora). */
export function buildTimezoneOptions() {
  const ids =
    typeof Intl.supportedValuesOf === 'function'
      ? Intl.supportedValuesOf('timeZone')
      : FALLBACK_TIMEZONES

  const seen = new Set()
  const parsed = []
  for (const rawId of ids) {
    const id = canonicalizeTimezoneId(rawId)
    if (seen.has(id)) continue
    seen.add(id)
    const { region, city } = inferRegionAndCity(id)
    parsed.push({ id, region, city })
  }

  const countByRegion = new Map()
  for (const row of parsed) {
    countByRegion.set(row.region, (countByRegion.get(row.region) ?? 0) + 1)
  }

  return parsed
    .map(({ id, region, city }) => {
      const count = countByRegion.get(region) ?? 1
      const label = buildLabel(region, city, count)
      const searchText = buildSearchText(region, city, id)
      return { id, label, region, city, searchText }
    })
    .sort((a, b) => {
      const c = a.region.localeCompare(b.region, 'es')
      if (c !== 0) return c
      return a.label.localeCompare(b.label, 'es')
    })
}

export function labelForTimezoneId(timeZoneId, options) {
  const list = options ?? buildTimezoneOptions()
  const id = canonicalizeTimezoneId(timeZoneId)
  return list.find((o) => o.id === id)?.label ?? timeZoneId
}

/** Resuelve texto libre a un id IANA si hay coincidencia única o exacta. */
export function resolveTimezoneQuery(query, options) {
  const list = options ?? buildTimezoneOptions()
  const raw = String(query ?? '').trim()
  if (!raw) return null

  const canonical = canonicalizeTimezoneId(raw)
  if (canonical !== raw || ARGENTINA_ALIAS_IDS.has(raw) || raw.toLowerCase().startsWith('america/argentina/')) {
    if (list.some((o) => o.id === canonical)) {
      return canonical
    }
  }

  const q = raw.toLowerCase()

  const byId = list.find((o) => o.id.toLowerCase() === q)
  if (byId) return byId.id

  const byLabel = list.filter((o) => o.label.toLowerCase() === q)
  if (byLabel.length === 1) return byLabel[0].id

  const bySearch = list.filter((o) => o.searchText.includes(q))
  if (bySearch.length === 1) return bySearch[0].id

  return null
}
