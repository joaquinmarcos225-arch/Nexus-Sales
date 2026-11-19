import { describe, expect, it } from 'vitest'
import { filtersFingerprint, searchResultsSummary } from './explorarSearchUi.js'

// mirror ExplorarPage helper (export if we consolidate later)
function filterOptionsByQuery(options, query) {
  const needle = String(query || '')
    .trim()
    .toLowerCase()
  if (!needle) return options
  return options.filter(
    ([value, text]) =>
      String(text || '')
        .toLowerCase()
        .includes(needle) ||
      String(value || '')
        .toLowerCase()
        .includes(needle),
  )
}

describe('filtersFingerprint', () => {
  it('cambia si cambia un filtro (vacía resultados viejos)', () => {
    const a = filtersFingerprint({
      names: '',
      titles: 'CEO',
      location: 'Chile',
      companyHq: '',
      companies: '',
      lookalikeUrls: '',
      industries: ['Restaurants'],
      headcounts: [],
      seniorities: ['Founder/Owner'],
      departments: [],
    })
    const b = filtersFingerprint({
      names: '',
      titles: 'CEO',
      location: 'Argentina',
      companyHq: '',
      companies: '',
      lookalikeUrls: '',
      industries: ['Restaurants'],
      headcounts: [],
      seniorities: ['Founder/Owner'],
      departments: [],
    })
    expect(a).not.toBe(b)
  })

  it('cambia si cambia sede/HQ aunque la ubicación del contacto sea igual', () => {
    const a = filtersFingerprint({
      names: '',
      titles: 'CEO',
      location: 'Argentina',
      companyHq: '',
      companies: '',
      lookalikeUrls: '',
      industries: [],
      headcounts: [],
      seniorities: [],
      departments: [],
    })
    const b = filtersFingerprint({
      names: '',
      titles: 'CEO',
      location: 'Argentina',
      companyHq: 'Argentina',
      companies: '',
      lookalikeUrls: '',
      industries: [],
      headcounts: [],
      seniorities: [],
      departments: [],
    })
    expect(a).not.toBe(b)
  })

  it('ordena listas para que el orden de chips no invalide', () => {
    const a = filtersFingerprint({
      names: '',
      titles: '',
      location: 'ar',
      companyHq: 'Chile',
      companies: '',
      lookalikeUrls: '',
      industries: ['B', 'A'],
      headcounts: [],
      seniorities: [],
      departments: [],
    })
    const b = filtersFingerprint({
      names: '',
      titles: '',
      location: 'AR',
      companyHq: 'chile',
      companies: '',
      lookalikeUrls: '',
      industries: ['A', 'B'],
      headcounts: [],
      seniorities: [],
      departments: [],
    })
    expect(a).toBe(b)
  })
})

describe('filterOptionsByQuery', () => {
  const opts = [
    ['restaurants', 'Restaurants'],
    ['software', 'Software Development'],
    ['hr', 'Human Resources'],
  ]
  it('filtra por etiqueta', () => {
    expect(filterOptionsByQuery(opts, 'soft')).toHaveLength(1)
    expect(filterOptionsByQuery(opts, 'human')).toHaveLength(1)
  })
})

describe('searchResultsSummary', () => {
  it('no tapa un pozo ICP grande (~300) con la 1ª hoja corta', () => {
    const s = searchResultsSummary({
      shown: 25,
      pageSize: 25,
      rawTotal: 312,
      pages: 13,
      morePending: false,
      totalIsEstimate: true,
    })
    expect(s.total).toBe(312)
    expect(s.dishonest).toBe(false)
    expect(s.totalIsEstimate).toBe(true)
    expect(s.showPageHint).toBe(true)
  })

  it('corrige total mentiroso de cerca (2 cards vs 23)', () => {
    const s = searchResultsSummary({
      shown: 2,
      pageSize: 25,
      rawTotal: 23,
      pages: 1,
      morePending: false,
    })
    expect(s.total).toBe(2)
    expect(s.dishonest).toBe(true)
    expect(s.totalIsEstimate).toBe(false)
  })

  it('marca pending cuando la página sigue llenándose', () => {
    const s = searchResultsSummary({
      shown: 10,
      pageSize: 25,
      rawTotal: 200,
      pages: 8,
      morePending: true,
    })
    expect(s.label).toBe('pending')
    expect(s.total).toBe(200)
  })
})
