export const ICP_MISSING_MESSAGE =
  'Completá al menos un parámetro del ICP para que Nexus pueda prospectar con criterio.'

const SKIP_VALUES = new Set([
  '',
  'no importante',
  'no importa',
  'sin preferencia',
  'cualquiera',
  '-',
  '--',
  'n/a',
  'na',
])

function normalized(value) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
}

export function icpLooksEmpty(value) {
  const v = normalized(value)
  if (v === '') {
    return true
  }
  return SKIP_VALUES.has(v)
}

export function icpHasMinimumSignal(fields) {
  return Object.values(fields).some((v) => !icpLooksEmpty(v))
}
