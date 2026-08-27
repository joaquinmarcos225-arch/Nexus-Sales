import { useEffect, useRef } from 'react'

const BLACK = '#000000'

/** Estrellas solo en login — movimiento suave, premium. */
const LOGIN_CFG = { starCount: 110, drift: 0.42 }

/** @param {number} n */
function seeded(n) {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453
  return x - Math.floor(x)
}

function buildStars(count, w, h) {
  const stars = []
  for (let i = 0; i < count; i += 1) {
    stars.push({
      bx: seeded(i + 1) * w,
      by: seeded(i + 101) * h,
      r: 0.5 + seeded(i + 201) * 1.8,
      phase: seeded(i + 301) * Math.PI * 2,
      twinkleSpeed: 0.8 + seeded(i + 401) * 1.6,
      vx: (seeded(i + 501) - 0.5) * 10,
      vy: (seeded(i + 601) - 0.5) * 7,
      bright: 0.28 + seeded(i + 701) * 0.55,
      cross: seeded(i + 801) > 0.82,
    })
  }
  return stars
}

function drawStar(ctx, x, y, r, alpha, cross) {
  const glow = ctx.createRadialGradient(x, y, 0, x, y, r * 3.5)
  glow.addColorStop(0, `rgba(248, 72, 72, ${alpha * 0.9})`)
  glow.addColorStop(0.4, `rgba(220, 38, 38, ${alpha * 0.35})`)
  glow.addColorStop(1, 'rgba(220, 38, 38, 0)')
  ctx.fillStyle = glow
  ctx.beginPath()
  ctx.arc(x, y, r * 3.5, 0, Math.PI * 2)
  ctx.fill()

  ctx.fillStyle = `rgba(254, 226, 226, ${Math.min(1, alpha + 0.2)})`
  ctx.beginPath()
  ctx.arc(x, y, r, 0, Math.PI * 2)
  ctx.fill()

  if (cross) {
    ctx.strokeStyle = `rgba(252, 165, 165, ${alpha * 0.7})`
    ctx.lineWidth = 0.6
    ctx.beginPath()
    ctx.moveTo(x - r * 1.8, y)
    ctx.lineTo(x + r * 1.8, y)
    ctx.moveTo(x, y - r * 1.8)
    ctx.lineTo(x, y + r * 1.8)
    ctx.stroke()
  }
}

/**
 * Fondo de login — estrellas rojas en movimiento.
 * @param {{ className?: string }} props
 */
export function VideoLikeMotionBackground({ className = '' }) {
  const canvasRef = useRef(null)
  const rafRef = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) {
      return undefined
    }

    const ctx = canvas.getContext('2d', { alpha: false })
    if (!ctx) {
      return undefined
    }

    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    let running = true
    let w = 0
    let h = 0
    let dpr = 1
    let stars = []

    function resize() {
      const parent = canvas.parentElement
      if (!parent) {
        return
      }
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = Math.max(1, parent.clientWidth)
      h = Math.max(1, parent.clientHeight)
      canvas.width = Math.floor(w * dpr)
      canvas.height = Math.floor(h * dpr)
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      stars = buildStars(LOGIN_CFG.starCount, w, h)
    }

    function paint(time) {
      ctx.fillStyle = BLACK
      ctx.fillRect(0, 0, w, h)

      const drift = LOGIN_CFG.drift
      for (let i = 0; i < stars.length; i += 1) {
        const s = stars[i]
        let x = (s.bx + time * s.vx * drift) % w
        let y = (s.by + time * s.vy * drift) % h
        if (x < 0) {
          x += w
        }
        if (y < 0) {
          y += h
        }
        const twinkle = s.bright * (0.5 + 0.5 * Math.sin(time * s.twinkleSpeed + s.phase))
        drawStar(ctx, x, y, s.r, twinkle, s.cross)
      }
    }

    function startLoop() {
      const loop = (now) => {
        if (!running) {
          return
        }
        paint(now * 0.001)
        rafRef.current = requestAnimationFrame(loop)
      }
      rafRef.current = requestAnimationFrame(loop)
    }

    resize()
    const ro = new ResizeObserver(() => resize())
    if (canvas.parentElement) {
      ro.observe(canvas.parentElement)
    }

    if (media.matches) {
      paint(0)
    } else {
      startLoop()
    }

    const onMotionChange = () => {
      cancelAnimationFrame(rafRef.current)
      if (media.matches) {
        paint(0)
      } else {
        startLoop()
      }
    }
    media.addEventListener('change', onMotionChange)

    return () => {
      running = false
      cancelAnimationFrame(rafRef.current)
      ro.disconnect()
      media.removeEventListener('change', onMotionChange)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className={['pointer-events-none absolute inset-0 h-full w-full', className].filter(Boolean).join(' ')}
      aria-hidden
    />
  )
}
