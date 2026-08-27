import { useEffect, useRef } from 'react'
import { buildNStrokes, drawNStroke } from './nexusNGeometry.js'

/**
 * Isotipo «N» formada por trazos luminosos de estrella (misma marca que el login).
 * @param {{ size?: number, className?: string, title?: string, animate?: boolean }} props
 */
export function NexusStarLogo({ size = 32, className = '', title = 'Nexus Sales', animate = true }) {
  const canvasRef = useRef(null)
  const rafRef = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) {
      return undefined
    }

    const ctx = canvas.getContext('2d', { alpha: true })
    if (!ctx) {
      return undefined
    }

    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const px = Math.max(16, size)
    canvas.width = Math.floor(px * dpr)
    canvas.height = Math.floor(px * dpr)
    canvas.style.width = `${px}px`
    canvas.style.height = `${px}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    const cx = px / 2
    const cy = px / 2 + px * 0.02
    const letterH = px * 0.72
    const strokes = buildNStrokes(cx, cy, letterH)

    function paint(time = 0) {
      ctx.clearRect(0, 0, px, px)
      const pulse = animate && !media.matches ? 0.82 + 0.18 * Math.sin(time * 2.2) : 1

      ctx.save()
      ctx.shadowColor = 'rgba(248, 72, 72, 0.65)'
      ctx.shadowBlur = px * 0.14 * pulse
      for (const stroke of strokes) {
        drawNStroke(ctx, stroke, 0.92 * pulse, Math.max(1.8, px * 0.09))
      }
      ctx.restore()

      // Un solo punto de acento (sin estrellas en las puntas)
      const dot = { x: strokes[0].x1, y: strokes[0].y2 }
      const dotR = Math.max(1.6, px * 0.055)
      ctx.fillStyle = `rgba(248, 72, 72, ${0.95 * pulse})`
      ctx.beginPath()
      ctx.arc(dot.x, dot.y, dotR, 0, Math.PI * 2)
      ctx.fill()
    }

    let running = true
    if (animate && !media.matches) {
      const loop = (now) => {
        if (!running) {
          return
        }
        paint(now * 0.001)
        rafRef.current = requestAnimationFrame(loop)
      }
      rafRef.current = requestAnimationFrame(loop)
    } else {
      paint(0)
    }

    const onMotion = () => {
      cancelAnimationFrame(rafRef.current)
      if (animate && !media.matches) {
        const loop = (now) => {
          if (!running) {
            return
          }
          paint(now * 0.001)
          rafRef.current = requestAnimationFrame(loop)
        }
        rafRef.current = requestAnimationFrame(loop)
      } else {
        paint(0)
      }
    }
    media.addEventListener('change', onMotion)

    return () => {
      running = false
      cancelAnimationFrame(rafRef.current)
      media.removeEventListener('change', onMotion)
    }
  }, [size, animate])

  return (
    <canvas
      ref={canvasRef}
      className={['block shrink-0', className].filter(Boolean).join(' ')}
      role="img"
      aria-label={title}
      title={title}
    />
  )
}
