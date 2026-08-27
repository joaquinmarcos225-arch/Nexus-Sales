import { NX_CHART } from '../../utils/chartTheme.js'

/** Etiqueta % dentro de tortas Recharts (oculta segmentos < 5%). */
export function PiePercentLabel({ cx, cy, midAngle, innerRadius, outerRadius, percent, payload }) {
  if (percent < 0.05) {
    return null
  }
  const RADIAN = Math.PI / 180
  const radius = innerRadius + (outerRadius - innerRadius) * 0.55
  const x = cx + radius * Math.cos(-midAngle * RADIAN)
  const y = cy + radius * Math.sin(-midAngle * RADIAN)
  const label = payload?.pctLabel ?? `${Math.round(percent * 100)}%`
  return (
    <text
      x={x}
      y={y}
      fill={NX_CHART.ink}
      textAnchor="middle"
      dominantBaseline="central"
      fontSize={10}
      fontWeight={600}
    >
      {label}
    </text>
  )
}

/** Valor encima de barras (conteo o %). */
export function BarTopLabel({ x, y, width, value, formatter }) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) {
    return null
  }
  const text = formatter ? formatter(n) : String(n)
  return (
    <text
      x={x + width / 2}
      y={y - 4}
      fill={NX_CHART.muted}
      textAnchor="middle"
      fontSize={10}
      fontWeight={600}
    >
      {text}
    </text>
  )
}
