import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './AuthContext.jsx'
import { TUTORIAL_STORAGE_KEY, tutorialStepsForUser } from '../data/tutorialSteps.js'

const TutorialContext = createContext(null)

function storageKey(userId) {
  return userId ? `${TUTORIAL_STORAGE_KEY}_${userId}` : TUTORIAL_STORAGE_KEY
}

export function TutorialProvider({ children }) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const userId = user?.user_id ?? user?.id

  const steps = useMemo(() => tutorialStepsForUser(user), [user])
  const [active, setActive] = useState(false)
  const [stepIndex, setStepIndex] = useState(0)
  const [completed, setCompleted] = useState(() => {
    if (!userId) return false
    try {
      return localStorage.getItem(storageKey(userId)) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    if (!userId) return
    try {
      setCompleted(localStorage.getItem(storageKey(userId)) === '1')
    } catch {
      setCompleted(false)
    }
  }, [userId])

  const currentStep = active ? steps[stepIndex] ?? null : null

  const markCompleted = useCallback(() => {
    if (userId) {
      try {
        localStorage.setItem(storageKey(userId), '1')
      } catch {
        /* ignore */
      }
    }
    setCompleted(true)
    setActive(false)
    setStepIndex(0)
  }, [userId])

  const startTutorial = useCallback(
    (fromStep = 0) => {
      const idx = Math.max(0, Math.min(fromStep, steps.length - 1))
      const step = steps[idx]
      setStepIndex(idx)
      setActive(true)
      if (step?.route && location.pathname !== step.route) {
        navigate(step.route)
      }
    },
    [steps, location.pathname, navigate],
  )

  const dismissTutorial = useCallback(() => {
    setActive(false)
    setStepIndex(0)
  }, [])

  const goNext = useCallback(() => {
    if (stepIndex >= steps.length - 1) {
      markCompleted()
      return
    }
    const nextIdx = stepIndex + 1
    const next = steps[nextIdx]
    setStepIndex(nextIdx)
    if (next?.route && location.pathname !== next.route) {
      navigate(next.route)
    }
  }, [stepIndex, steps, location.pathname, navigate, markCompleted])

  const goPrev = useCallback(() => {
    if (stepIndex <= 0) return
    const prevIdx = stepIndex - 1
    const prev = steps[prevIdx]
    setStepIndex(prevIdx)
    if (prev?.route && location.pathname !== prev.route) {
      navigate(prev.route)
    }
  }, [stepIndex, steps, location.pathname, navigate])

  const value = useMemo(
    () => ({
      active,
      stepIndex,
      steps,
      currentStep,
      completed,
      startTutorial,
      dismissTutorial,
      goNext,
      goPrev,
      markCompleted,
    }),
    [
      active,
      stepIndex,
      steps,
      currentStep,
      completed,
      startTutorial,
      dismissTutorial,
      goNext,
      goPrev,
      markCompleted,
    ],
  )

  return <TutorialContext.Provider value={value}>{children}</TutorialContext.Provider>
}

export function useTutorial() {
  const ctx = useContext(TutorialContext)
  if (!ctx) {
    throw new Error('useTutorial debe usarse dentro de TutorialProvider')
  }
  return ctx
}
