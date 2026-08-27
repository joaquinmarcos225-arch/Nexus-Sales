import { useCallback, useEffect, useLayoutEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTutorial } from '../../context/TutorialContext.jsx'

const PAD = 8

function useTargetRect(selector, stepIndex, pathname) {
  const [rect, setRect] = useState(null)

  const measure = useCallback(() => {
    if (!selector) {
      setRect(null)
      return
    }
    const el = document.querySelector(selector)
    if (!el) {
      setRect(null)
      return
    }
    const r = el.getBoundingClientRect()
    setRect({
      top: r.top - PAD,
      left: r.left - PAD,
      width: r.width + PAD * 2,
      height: r.height + PAD * 2,
    })
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selector])

  useLayoutEffect(() => {
    measure()
    const t1 = window.setTimeout(measure, 120)
    const t2 = window.setTimeout(measure, 400)
    return () => {
      window.clearTimeout(t1)
      window.clearTimeout(t2)
    }
  }, [measure, stepIndex, pathname])

  useEffect(() => {
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [measure])

  return rect
}

function cardPosition(rect) {
  if (!rect) {
    return { top: '50%', left: '50%', transform: 'translate(-50%, -50%)', maxWidth: '22rem' }
  }
  const below = rect.top + rect.height + 16
  const fitsBelow = below + 200 < window.innerHeight
  if (fitsBelow) {
    return {
      top: below,
      left: Math.max(16, Math.min(rect.left, window.innerWidth - 360)),
      maxWidth: '22rem',
    }
  }
  return {
    top: Math.max(16, rect.top - 200),
    left: Math.max(16, Math.min(rect.left, window.innerWidth - 360)),
    maxWidth: '22rem',
  }
}

export function TutorialOverlay() {
  const { active, currentStep, stepIndex, steps, goNext, goPrev, dismissTutorial, markCompleted } =
    useTutorial()
  const pathname = typeof window !== 'undefined' ? window.location.pathname : ''
  const rect = useTargetRect(active ? currentStep?.target : null, stepIndex, pathname)

  if (!active || !currentStep) {
    return null
  }

  const pos = cardPosition(rect)
  const isLast = stepIndex >= steps.length - 1

  return createPortal(
    <div className="fixed inset-0 z-[200]" role="dialog" aria-modal="true" aria-labelledby="nexus-tutorial-title">
      <div className="absolute inset-0 bg-zinc-900/55" onClick={dismissTutorial} aria-hidden />
      {rect ? (
        <div
          className="pointer-events-none absolute rounded-xl ring-2 ring-nx-brand ring-offset-2 ring-offset-transparent shadow-[0_0_0_9999px_rgba(15,23,42,0.55)]"
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
          }}
        />
      ) : null}
      <div
        className="absolute z-10 rounded-xl border border-nx-border bg-nx-card p-4 shadow-xl"
        style={pos}
      >
        <p className="text-[10px] font-semibold uppercase tracking-wider text-nx-brand">
          Tutorial · {stepIndex + 1} / {steps.length}
        </p>
        <h2 id="nexus-tutorial-title" className="mt-1 text-base font-semibold text-nx-ink">
          {currentStep.title}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-nx-muted">{currentStep.body}</p>
        {!rect ? (
          <p className="mt-2 text-xs text-zinc-700">
            Abrí el menú lateral si no ves el elemento resaltado.
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button type="button" className="nx-btn nx-btn-ghost text-xs" onClick={dismissTutorial}>
            Salir
          </button>
          <button
            type="button"
            className="nx-btn nx-btn-ghost text-xs"
            onClick={goPrev}
            disabled={stepIndex === 0}
          >
            Anterior
          </button>
          <button type="button" className="nx-btn nx-btn-primary text-xs" onClick={isLast ? markCompleted : goNext}>
            {isLast ? 'Finalizar' : 'Siguiente'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
