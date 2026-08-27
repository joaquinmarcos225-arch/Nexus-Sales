/** Paleta Recharts alineada a tokens Nexus (`index.css` @theme). */



export const NX_CHART = {

  brand: '#dc2626',

  brandHover: '#b91c1c',

  brandDark: '#991b1b',

  brandDeep: '#7f1d1d',

  brandLight: '#f87171',

  brandSoft: '#fecaca',

  brandMuted: '#fca5a5',

  ink: '#0f172a',

  muted: '#64748b',

  subtle: '#94a3b8',

  grid: '#e2e8f0',

  surface: '#ffffff',

  empty: '#d1d5db',

}



/** Series categóricas (pie, barras agrupadas, scatter). */

export const NX_CHART_SERIES = [

  NX_CHART.brand,

  NX_CHART.brandHover,

  NX_CHART.brandDark,

  NX_CHART.brandDeep,

  NX_CHART.brandLight,

  NX_CHART.brandSoft,

  NX_CHART.subtle,

  NX_CHART.muted,

]



/** Barras agrupadas por métrica (volumen campaña / equipo). */

export const NX_CHART_VOLUME = {

  prospectos: NX_CHART.subtle,

  mensajes: NX_CHART.brandSoft,

  respuestas: NX_CHART.brandHover,

  interesados: NX_CHART.brand,

  reuniones: NX_CHART.brandDeep,

  tasaRespuesta: NX_CHART.brand,

}



/** Sentimiento en pie charts (positivo → neutro → negativo). */

export const NX_CHART_SENTIMENT = [NX_CHART.brandDeep, NX_CHART.brand, NX_CHART.brandSoft]



export const NX_CHART_GRID = { strokeDasharray: '3 3', stroke: NX_CHART.grid }



export const NX_CHART_MARGIN = {

  default: { top: 8, right: 12, left: 0, bottom: 8 },

  labeledX: { top: 16, right: 12, left: 0, bottom: 48 },

}



export const NX_CHART_AXIS_TICK = { fontSize: 10, fill: NX_CHART.muted }

export const NX_CHART_Y_TICK = { fontSize: 11, fill: NX_CHART.muted }



export const NX_CHART_AXIS = { tickLine: false, axisLine: { stroke: NX_CHART.grid } }



export const NX_CHART_TOOLTIP = {

  contentStyle: {

    borderRadius: 8,

    border: `1px solid ${NX_CHART.grid}`,

    fontSize: 12,

    boxShadow: '0 4px 12px rgba(15, 23, 42, 0.08)',

  },

  labelStyle: { color: NX_CHART.ink, fontWeight: 600, marginBottom: 4 },

  itemStyle: { color: NX_CHART.muted },

}



export const NX_CHART_LEGEND = {

  wrapperStyle: { fontSize: 11, paddingTop: 8 },

  iconType: 'circle',

  iconSize: 8,

}



export const NX_CHART_BAR = {

  radius: [4, 4, 0, 0],

  maxBarSize: 40,

}



/** Tasa 0–1 → "12.3%" */

export function formatPctRate(rate, digits = 1) {

  const n = Number(rate)

  if (!Number.isFinite(n)) {

    return '0%'

  }

  return `${(n * 100).toFixed(digits)}%`

}



/** Valor ya en escala 0–100 → "12.3%" */

export function formatPctNumber(value, digits = 1) {

  const n = Number(value)

  if (!Number.isFinite(n)) {

    return '0%'

  }

  return `${n.toFixed(digits)}%`

}



export function formatPctTooltip(value, name) {

  return [`${value}%`, name || 'Tasa respuesta']

}



export function sumBy(data, key) {

  return (data || []).reduce((acc, row) => acc + (Number(row?.[key]) || 0), 0)

}



export function averageBy(data, key) {

  const rows = data || []

  if (!rows.length) {

    return 0

  }

  return sumBy(rows, key) / rows.length

}



/** Agrega pct y pctLabel a cada slice de torta. */

export function enrichSlicesWithPct(data, { valueKey = 'value', nameKey = 'name' } = {}) {

  const total = sumBy(data, valueKey)

  return (data || []).map((row) => {

    const value = Number(row?.[valueKey]) || 0

    const pct = total > 0 ? Math.round((value / total) * 1000) / 10 : 0

    const name = row?.[nameKey] ?? ''

    return {

      ...row,

      pct,

      pctLabel: `${pct}%`,

      legendLabel: total > 0 ? `${name} (${pct}%)` : name,

    }

  })

}



/** Tooltip torta: valor + % del total. */

export function pieTooltipWithPct(value, name, props) {

  const payload = props?.payload

  const pct = payload?.pct ?? payload?.pctLabel

  const pctText = typeof pct === 'number' ? formatPctNumber(pct) : pct

  return [`${value}${pctText ? ` · ${pctText}` : ''}`, name]

}



/** Tooltip conteos con promedio opcional en el nombre de serie. */

export function countTooltipWithAvg(avg) {

  return (value, name) => {

    const suffix = Number.isFinite(avg) ? ` · prom. ${Number(avg).toFixed(1)}` : ''

    return [`${value}${suffix}`, name]

  }

}



/** Subtítulo bajo título de gráfico con promedio. */

export function chartAvgCaption(label, value, { suffix = '', digits = 1 } = {}) {

  const n = Number(value)

  if (!Number.isFinite(n)) {

    return null

  }

  const formatted = suffix === '%' ? formatPctNumber(n, digits) : n.toFixed(digits)

  return `${label}: ${formatted}${suffix && suffix !== '%' ? ` ${suffix}` : ''}`

}


