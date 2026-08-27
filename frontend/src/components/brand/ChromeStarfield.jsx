import { useEffect, useRef } from 'react'

/** @param {number} n */
function seeded(n) {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453
  return x - Math.floor(x)
}

function buildStars(count, w, h, seed) {
  const stars = []
  for (let i = 0; i < count; i += 1) {
    const s = seed + i
    stars.push({
      bx: seeded(s + 1) * w,
      by: seeded(s + 101) * h,
      r: 0.35 + seeded(s + 201) * 1.1,
      phase: seeded(s + 301) * Math.PI * 2,
      twinkleSpeed: 0.6 + seeded(s + 401) * 1.2,
      vx: (seeded(s + 501) - 0.5) * 8,
      vy: (seeded(s + 601) - 0.5) * 5,
      bright: 0.34 + seeded(s + 701) * 0.52,
      cross: seeded(s + 801) > 0.88,
    })
  }
  return stars
}

function drawStar(ctx, x, y, r, alpha, cross) {
  const glow = ctx.createRadialGradient(x, y, 0, x, y, r * 3.2)
  glow.addColorStop(0, `rgba(248, 72, 72, ${alpha * 0.95})`)
  glow.addColorStop(0.45, `rgba(220, 38, 38, ${alpha * 0.38})`)
  glow.addColorStop(1, 'rgba(220, 38, 38, 0)')
  ctx.fillStyle = glow
  ctx.beginPath()
  ctx.arc(x, y, r * 3.2, 0, Math.PI * 2)
  ctx.fill()

  ctx.fillStyle = `rgba(254, 226, 226, ${Math.min(1, alpha + 0.22)})`
  ctx.beginPath()
  ctx.arc(x, y, r, 0, Math.PI * 2)
  ctx.fill()

  if (cross) {
    ctx.strokeStyle = `rgba(252, 165, 165, ${alpha * 0.55})`
    ctx.lineWidth = 0.5
    ctx.beginPath()
    ctx.moveTo(x - r * 1.6, y)
    ctx.lineTo(x + r * 1.6, y)
    ctx.moveTo(x, y - r * 1.6)
    ctx.lineTo(x, y + r * 1.6)
    ctx.stroke()
  }
}

function drawShootingStar(ctx, meteor) {
  const t = meteor.life / meteor.maxLife
  const alpha = (1 - t) * 0.9
  const x = meteor.x + meteor.vx * meteor.life
  const y = meteor.y + meteor.vy * meteor.life
  const tailX = x - meteor.len * 0.75
  const tailY = y - meteor.len * 0.38

  const grad = ctx.createLinearGradient(tailX, tailY, x, y)
  grad.addColorStop(0, 'rgba(220, 38, 38, 0)')
  grad.addColorStop(0.55, `rgba(248, 113, 113, ${alpha * 0.35})`)
  grad.addColorStop(1, `rgba(254, 226, 226, ${alpha})`)

  ctx.strokeStyle = grad
  ctx.lineWidth = 1.4
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(tailX, tailY)
  ctx.lineTo(x, y)
  ctx.stroke()

  drawStar(ctx, x, y, 0.9, alpha * 0.85, false)
}

const PRESETS = {
  sidebar: { starCount: 28, drift: 0.2, seed: 120, shootMin: 3, shootMax: 6.5 },
  topbar: { starCount: 16, drift: 0.16, seed: 840, shootMin: 4, shootMax: 9 },
}

/**
 * Estrellas rojas sutiles + estrella fugaz ocasional (sidebar / topbar).
 * @param {{ variant?: 'sidebar' | 'topbar', className?: string }} props
 */
export function ChromeStarfield({ variant = 'sidebar', className = '' }) {
  const canvasRef = useRef(null)
  const rafRef = useRef(0)
  const cfg = PRESETS[variant] || PRESETS.sidebar

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
    let running = true
    let w = 0
    let h = 0
    let dpr = 1
    let stars = []
    /** @type {{ x: number, y: number, vx: number, vy: number, life: number, maxLife: number, len: number } | null} */
    let meteor = null
    let nextShootAt = 0
    let lastFrame = 0

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
      stars = buildStars(cfg.starCount, w, h, cfg.seed)
    }

    function spawnMeteor(now) {
      const angle = 0.25 + seeded(now * 0.017) * 0.35
      const speed = 140 + seeded(now * 0.031) * 90
      meteor = {
        x: seeded(now * 0.009) * w * 0.75,
        y: seeded(now * 0.013) * h * 0.45,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 0,
        maxLife: 0.45 + seeded(now * 0.021) * 0.35,
        len: 36 + seeded(now * 0.027) * 44,
      }
      nextShootAt =
        now + (cfg.shootMin + seeded(now * 0.007) * (cfg.shootMax - cfg.shootMin)) * 1000
    }

    function paint(now) {
      const time = now * 0.001
      const dt = lastFrame ? Math.min(0.05, (now - lastFrame) * 0.001) : 0.016
      lastFrame = now

      ctx.clearRect(0, 0, w, h)

      for (let i = 0; i < stars.length; i += 1) {
        const s = stars[i]
        let x = (s.bx + time * s.vx * cfg.drift) % w
        let y = (s.by + time * s.vy * cfg.drift) % h
        if (x < 0) {
          x += w
        }
        if (y < 0) {
          y += h
        }
        const twinkle = s.bright * (0.55 + 0.45 * Math.sin(time * s.twinkleSpeed + s.phase))
        drawStar(ctx, x, y, s.r, twinkle, s.cross)
      }

      if (!meteor && now >= nextShootAt) {
        spawnMeteor(now)
      }

      if (meteor) {
        meteor.life += dt
        if (meteor.life >= meteor.maxLife) {
          meteor = null
        } else {
          drawShootingStar(ctx, meteor)
        }
      }
    }

    function startLoop() {
      nextShootAt = performance.now() + cfg.shootMin * 1000
      const loop = (now) => {
        if (!running) {
          return
        }
        paint(now)
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
  }, [cfg.drift, cfg.seed, cfg.shootMax, cfg.shootMin, cfg.starCount])

  return (
    <canvas
      ref={canvasRef}
      className={['pointer-events-none absolute inset-0 h-full w-full', className].filter(Boolean).join(' ')}
      aria-hidden
    />
  )
}
