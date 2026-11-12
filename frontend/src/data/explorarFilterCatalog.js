/**
 * Fallback del catálogo Explorar (misma fuente que backend explorar_filter_catalog.py).
 * En runtime la página pide GET .../explorar/filter-catalog; esto es respaldo offline/cache.
 * No agregar valores sin verificar Prospeo Search Person.
 */

export const EXPLORAR_CATALOG_VERSION = '2026-11-04-prospeo'

/** @type {[string, string][]} value, label — orden = chips visibles primero */
export const FALLBACK_INDUSTRIES = [
  ['Software Development', 'Software'],
  ['Online and Mail Order Retail', 'Ecommerce'],
  ['Restaurants', 'Restaurantes'],
  ['Retail Pharmacies', 'Farmacias'],
  ['General Retail', 'Retail'],
  ['IT Services and IT Consulting', 'Servicios IT'],
  ['Food and Beverage Services', 'Gastronomía'],
  ['Consumer Goods', 'Consumo masivo'],
  ['Marketing Services', 'Marketing'],
  ['Transportation, Logistics, Supply Chain and Storage', 'Logística y transporte'],
  ['Technology, Information and Internet', 'Tecnología e internet'],
  ['Internet Marketplace Platforms', 'Marketplaces'],
  ['Retail Apparel and Fashion', 'Moda / retail'],
  ['Retail Groceries', 'Supermercados'],
  ['Consumer Services', 'Servicios al consumidor'],
  ['Mobile Food Services', 'Food trucks / comida móvil'],
  ['Food and Beverage Retail', 'Retail comida / bebida'],
  ['Food and Beverage Manufacturing', 'Alimentos (fábrica)'],
  ['Wholesale Food and Beverage', 'Mayorista alimentos'],
  ['Breweries', 'Cervecerías'],
  ['Retail Health and Personal Care Products', 'Cuidado personal (retail)'],
  ['Wholesale Drugs and Sundries', 'Mayorista farma'],
  ['Pharmaceutical Manufacturing', 'Farmacéutica'],
  ['Hospitals and Health Care', 'Salud'],
  ['Medical Practices', 'Consultorios médicos'],
  ['Dentists', 'Odontología'],
  ['Wellness and Fitness Services', 'Wellness / fitness'],
  ['Cosmetics', 'Cosmética'],
  ['Personal Care Product Manufacturing', 'Cuidado personal (fábrica)'],
  ['Vehicle Repair and Maintenance', 'Talleres / autos'],
  ['Pet Services', 'Mascotas'],
  ['Facilities Services', 'Facilities'],
  ['Events Services', 'Eventos'],
  ['Design Services', 'Diseño'],
  ['Professional Services', 'Servicios profesionales'],
  ['Accommodation Services', 'Alojamiento'],
  ['Hospitality', 'Hotelería y turismo'],
  ['Financial Services', 'Servicios financieros'],
  ['Banking', 'Banca'],
  ['Insurance', 'Seguros'],
  ['Real Estate', 'Inmobiliaria'],
  ['Construction', 'Construcción'],
  ['Advertising Services', 'Publicidad'],
  ['Business Consulting and Services', 'Consultoría'],
  ['Staffing and Recruiting', 'RRHH y selección'],
  ['Human Resources Services', 'Servicios RRHH'],
  ['Legal Services', 'Legales'],
  ['Accounting', 'Contabilidad'],
  ['Telecommunications', 'Telecomunicaciones'],
  ['Motor Vehicle Manufacturing', 'Automotriz'],
  ['General Manufacturing', 'Manufactura'],
  ['General Wholesale', 'Mayorista'],
  ['E-Learning Providers', 'E-learning'],
  ['Higher Education', 'Educación superior'],
  ['Education Administration Programs', 'Educación (admin)'],
  ['Primary and Secondary Education', 'Educación primaria/secundaria'],
]

export const FALLBACK_SENIORITIES = [
  ['Founder/Owner', 'Dueño / Fundador'],
  ['C-Suite', 'C-Level'],
  ['Partner', 'Partner'],
  ['Vice President', 'VP'],
  ['Head', 'Head'],
  ['Director', 'Director'],
  ['Manager', 'Gerente'],
  ['Senior', 'Senior'],
  ['Entry', 'Junior'],
]

export const FALLBACK_DEPARTMENTS = [
  ['C-Suite', 'Dirección'],
  ['Sales', 'Ventas'],
  ['Marketing', 'Marketing'],
  ['Finance', 'Finanzas'],
  ['Human Resources', 'RRHH'],
  ['Operations', 'Operaciones'],
  ['Information Technology', 'Sistemas / IT'],
  ['Engineering & Technical', 'Ingeniería'],
  ['Product', 'Producto'],
  ['Design', 'Diseño'],
  ['Legal', 'Legales'],
]

export const FALLBACK_HEADCOUNTS = [
  ['1-10', '1–10'],
  ['11-20', '11–20'],
  ['21-50', '21–50'],
  ['51-100', '51–100'],
  ['101-200', '101–200'],
  ['201-500', '201–500'],
  ['501-1000', '501–1.000'],
  ['1001-2000', '1.001–2.000'],
  ['2001-5000', '2.001–5.000'],
  ['5001-10000', '5.001–10.000'],
  ['10000+', '+10.000'],
]

export function pairsFromCatalog(rows) {
  if (!Array.isArray(rows) || !rows.length) return null
  const out = []
  for (const row of rows) {
    const value = String(row?.value || '').trim()
    const label = String(row?.label || value).trim()
    if (value) out.push([value, label || value])
  }
  return out.length ? out : null
}

/** Conserva solo values presentes en options [[value, label], ...]. */
export function keepAllowed(values, options) {
  const allowed = new Set(
    (Array.isArray(options) ? options : []).map((row) => (Array.isArray(row) ? row[0] : row)).filter(Boolean),
  )
  const out = []
  const seen = new Set()
  for (const raw of values || []) {
    const v = String(raw || '').trim()
    if (!v || !allowed.has(v) || seen.has(v)) continue
    seen.add(v)
    out.push(v)
  }
  return out
}
