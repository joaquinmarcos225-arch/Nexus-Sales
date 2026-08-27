import { useEffect, useRef } from 'react'

/** Debe coincidir con AuthEnterTransition: MORPH_MS + HOLD_MS */
export const TRANSITION_REVEAL_START_S = 3.6

function easeOutCubic(t) {
  const x = Math.min(1, Math.max(0, t))
  return 1 - (1 - x) ** 3
}

function easeInOut(t) {
  const x = Math.min(1, Math.max(0, t))
  return x < 0.5 ? 2 * x * x : 1 - (-2 * x + 2) ** 2 / 2
}

/**
 * N como un solo trazo continuo: arriba-izq → abajo-izq → arriba-der → abajo-der.
 * @param {number} cx
 * @param {number} cy
 * @param {number} height
 * @returns {{ x: number, y: number }[]}
 */
function buildNPath(cx, cy, height) {
  const w = height * 0.56
  const left = cx - w / 2
  const right = cx + w / 2
  const top = cy - height / 2
  const bottom = cy + height / 2
  return [
    { x: left, y: top },
    { x: left, y: bottom },
    { x: right, y: top },
    { x: right, y: bottom },
  ]
}

/**
 * S = 3 segmentos que se tocan, dibujados de abajo → arriba:
 * 1) Abajo: abajo-izq → punta (arriba-der)
 * 2) Medio: desde esa punta → izquierda (sigue subiendo el trazo)
 * 3) Arriba: desde ahí → arriba-der
 * @param {number} cx
 * @param {number} cy
 * @param {number} height
 * @returns {{ x: number, y: number }[][]}
 */
function buildSStrokes(cx, cy, height) {
  const w = height * 0.56
  const left = cx - w / 2
  const right = cx + w / 2
  const top = cy - height / 2
  const bottom = cy + height / 2

  const tipBottom = {
    x: right - w * 0.06,
    y: cy + height * 0.06,
  }
  const startBottom = {
    x: left + w * 0.02,
    y: bottom - height * 0.06,
  }
  const midLeft = {
    x: left + w * 0.06,
    y: cy - height * 0.08,
  }
  const tipTop = {
    x: right - w * 0.02,
    y: top + height * 0.08,
  }

  // Orden y dirección: el “bolígrafo” sube de abajo hacia arriba
  return [
    [startBottom, tipBottom],
    [tipBottom, midLeft],
    [midLeft, tipTop],
  ]
}

/**
 * @param {{ x: number, y: number }[]} points
 */
function pathTotalLength(points) {
  let len = 0
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1]
    const b = points[i]
    len += Math.hypot(b.x - a.x, b.y - a.y)
  }
  return len
}

/**
 * Puntos desde el inicio hasta `progress` (0–1) a lo largo del path.
 * @param {{ x: number, y: number }[]} points
 * @param {number} progress
 * @returns {{ poly: { x: number, y: number }[], tip: { x: number, y: number } | null }}
 */
function slicePath(points, progress) {
  if (points.length < 2 || progress <= 0) {
    return { poly: points.length ? [points[0]] : [], tip: points[0] || null }
  }
  if (progress >= 0.999) {
    return { poly: points, tip: points[points.length - 1] }
  }

  const total = pathTotalLength(points)
  let remain = total * progress
  /** @type {{ x: number, y: number }[]} */
  const poly = [points[0]]

  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1]
    const b = points[i]
    const seg = Math.hypot(b.x - a.x, b.y - a.y)
    if (remain >= seg) {
      poly.push(b)
      remain -= seg
      continue
    }
    const t = seg > 0 ? remain / seg : 0
    const tip = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
    poly.push(tip)
    return { poly, tip }
  }

  return { poly: points, tip: points[points.length - 1] }
}

/**
 * Dibuja un path continuo (una sola punta que avanza).
 * @param {CanvasRenderingContext2D} ctx
 * @param {{ x: number, y: number }[]} points
 * @param {number} progress
 * @param {number} alpha
 * @param {number} lineWidth
 */
function drawPartialPath(ctx, points, progress, alpha, lineWidth = 3.4) {
  if (progress <= 0 || points.length < 2) return

  const { poly, tip } = slicePath(points, progress)
  if (poly.length < 2 || !tip) return

  const start = poly[0]
  const grad = ctx.createLinearGradient(start.x, start.y, tip.x, tip.y)
  grad.addColorStop(0, `rgba(185, 28, 28, ${alpha * 0.45})`)
  grad.addColorStop(0.7, `rgba(239, 68, 68, ${alpha * 0.85})`)
  grad.addColorStop(1, `rgba(254, 226, 226, ${alpha})`)

  ctx.strokeStyle = grad
  ctx.lineWidth = lineWidth
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  ctx.moveTo(poly[0].x, poly[0].y)
  for (let i = 1; i < poly.length; i += 1) {
    ctx.lineTo(poly[i].x, poly[i].y)
  }
  ctx.stroke()

  if (progress > 0.02 && progress < 0.995) {
    const glow = ctx.createRadialGradient(tip.x, tip.y, 0, tip.x, tip.y, lineWidth * 2.2)
    glow.addColorStop(0, `rgba(254, 226, 226, ${alpha * 0.95})`)
    glow.addColorStop(1, 'rgba(220, 38, 38, 0)')
    ctx.fillStyle = glow
    ctx.beginPath()
    ctx.arc(tip.x, tip.y, lineWidth * 2.2, 0, Math.PI * 2)
    ctx.fill()
  }
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {{ x: number, y: number }[]} points
 * @param {number} alpha
 */
function drawCompletedPath(ctx, points, alpha) {
  ctx.save()
  ctx.shadowColor = 'rgba(220, 38, 38, 0.55)'
  ctx.shadowBlur = 16
  drawPartialPath(ctx, points, 1, alpha, 3.4)
  ctx.restore()
}

/**
 * Aparición de N (trazo continuo) y S (3 diagonales /) en paralelo.
 * @param {{ phase: 'morph' | 'hold' | 'reveal' }} props
 */
export function TransitionNBurst({ phase }) {
  const canvasRef = useRef(null)
  const rafRef = useRef(0)
  const startedAtRef = useRef(0)
  const phaseRef = useRef(phase)

  useEffect(() => {
    phaseRef.current = phase
  }, [phase])

  useEffect(() => {
    startedAtRef.current = performance.now()
    const canvas = canvasRef.current
    if (!canvas) return undefined

    const ctx = canvas.getContext('2d', { alpha: true })
    if (!ctx) return undefined

    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    let running = true
    let w = 0
    let h = 0
    let dpr = 1
    /** @type {{ x: number, y: number }[]} */
    let nPath = []
    /** @type {{ x: number, y: number }[][]} */
    let sStrokes = []
    let originX = 0
    let originY = 0

    function resize() {
      const parent = canvas.parentElement
      if (!parent) return
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = Math.max(1, parent.clientWidth)
      h = Math.max(1, parent.clientHeight)
      canvas.width = Math.floor(w * dpr)
      canvas.height = Math.floor(h * dpr)
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      const letterH = Math.min(w, h) * 0.28
      const gap = letterH * 0.4
      const blockW = letterH * 0.56 + letterH * 0.58 + gap
      const nCx = w / 2 - blockW / 2 + letterH * 0.28
      const sCx = w / 2 + blockW / 2 - letterH * 0.29
      const cy = h * 0.42
      originX = w / 2
      originY = cy
      nPath = buildNPath(nCx, cy, letterH)
      sStrokes = buildSStrokes(sCx, cy, letterH)
    }

    function paint(now) {
      const elapsed = (now - startedAtRef.current) * 0.001
      const currentPhase = phaseRef.current
      ctx.clearRect(0, 0, w, h)

      if (media.matches) {
        drawCompletedPath(ctx, nPath, 0.9)
        sStrokes.forEach((stroke) => drawCompletedPath(ctx, stroke, 0.9))
        return
      }

      let globalAlpha = 1
      if (currentPhase === 'reveal') {
        globalAlpha = Math.max(0, 1 - (elapsed - TRANSITION_REVEAL_START_S) / 1)
      }
      if (globalAlpha <= 0.01) return

      const fadeIn = easeOutCubic(Math.min(1, elapsed / 0.35))
      const drawStart = 0.18
      const drawDur = 1.35
      const raw = Math.min(1, Math.max(0, (elapsed - drawStart) / drawDur))
      const progress = easeInOut(raw)
      const settle = easeOutCubic(Math.min(1, Math.max(0, (elapsed - (drawStart + drawDur)) / 0.35)))

      const scale = 0.94 + 0.06 * easeOutCubic(Math.min(1, progress * 1.15))
      const strokeAlpha = (0.35 + 0.65 * fadeIn) * globalAlpha

      ctx.save()
      ctx.globalAlpha = globalAlpha
      ctx.translate(originX, originY)
      ctx.scale(scale, scale)
      ctx.translate(-originX, -originY)

      const bloom = 0.04 + 0.1 * progress
      const glow = ctx.createRadialGradient(originX, originY, 0, originX, originY, Math.min(w, h) * 0.38)
      glow.addColorStop(0, `rgba(220,38,38,${bloom})`)
      glow.addColorStop(1, 'rgba(220,38,38,0)')
      ctx.fillStyle = glow
      ctx.fillRect(0, 0, w, h)

      drawPartialPath(ctx, nPath, progress, strokeAlpha, 3.4)
      // S: un solo progreso de abajo → arriba a lo largo de los 3 tramos
      const sProgress = progress
      sStrokes.forEach((stroke, i) => {
        const third = 1 / sStrokes.length
        const start = i * third
        const local = Math.min(1, Math.max(0, (sProgress - start) / third))
        drawPartialPath(ctx, stroke, local, strokeAlpha, 3.8)
      })

      if (progress >= 0.995) {
        const holdPulse = 0.82 + 0.1 * Math.sin(elapsed * 1.6) * settle
        drawCompletedPath(ctx, nPath, holdPulse * strokeAlpha)
        sStrokes.forEach((stroke) => drawCompletedPath(ctx, stroke, holdPulse * strokeAlpha))
      }

      ctx.restore()
    }

    function loop(now) {
      if (!running) return
      paint(now)
      rafRef.current = requestAnimationFrame(loop)
    }

    resize()
    const ro = new ResizeObserver(() => resize())
    if (canvas.parentElement) ro.observe(canvas.parentElement)
    paint(performance.now())
    rafRef.current = requestAnimationFrame(loop)

    return () => {
      running = false
      cancelAnimationFrame(rafRef.current)
      ro.disconnect()
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="nx-auth-curtain__stars pointer-events-none absolute inset-0 z-[1]"
      aria-hidden
    />
  )
}
