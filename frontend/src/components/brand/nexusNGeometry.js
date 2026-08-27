/** Geometría compartida de la «N» de marca (login + chrome). */

/**
 * @param {number} cx
 * @param {number} cy
 * @param {number} height
 */
export function buildNStrokes(cx, cy, height) {
  const w = height * 0.58
  const left = cx - w / 2
  const right = cx + w / 2
  const top = cy - height / 2
  const bottom = cy + height / 2

  return [
    { x1: left, y1: top, x2: left, y2: bottom },
    { x1: left, y1: bottom, x2: right, y2: top },
    { x1: right, y1: top, x2: right, y2: bottom },
  ]
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {{ x1: number, y1: number, x2: number, y2: number }} stroke
 * @param {number} alpha
 * @param {number} [lineWidth]
 */
export function drawNStroke(ctx, stroke, alpha, lineWidth = 2.8) {
  const lineGrad = ctx.createLinearGradient(stroke.x1, stroke.y1, stroke.x2, stroke.y2)
  lineGrad.addColorStop(0, `rgba(220, 38, 38, ${alpha * 0.45})`)
  lineGrad.addColorStop(0.5, `rgba(248, 113, 113, ${alpha * 0.85})`)
  lineGrad.addColorStop(1, `rgba(254, 226, 226, ${alpha * 0.98})`)
  ctx.strokeStyle = lineGrad
  ctx.lineWidth = lineWidth
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(stroke.x1, stroke.y1)
  ctx.lineTo(stroke.x2, stroke.y2)
  ctx.stroke()
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} x
 * @param {number} y
 * @param {number} r
 * @param {number} alpha
 */
export function drawNSpark(ctx, x, y, r, alpha) {
  const glow = ctx.createRadialGradient(x, y, 0, x, y, r * 3.2)
  glow.addColorStop(0, `rgba(254, 226, 226, ${alpha * 0.95})`)
  glow.addColorStop(0.45, `rgba(248, 72, 72, ${alpha * 0.4})`)
  glow.addColorStop(1, 'rgba(220, 38, 38, 0)')
  ctx.fillStyle = glow
  ctx.beginPath()
  ctx.arc(x, y, r * 3.2, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = `rgba(255, 255, 255, ${Math.min(1, alpha * 0.9)})`
  ctx.beginPath()
  ctx.arc(x, y, r * 0.75, 0, Math.PI * 2)
  ctx.fill()
}
