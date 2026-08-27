import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useLocation } from 'react-router-dom'
import { TransitionNBurst } from '../components/brand/TransitionNBurst.jsx'
import { LoginWaveBackground } from '../components/brand/LoginWaveBackground.jsx'
import { NexusWordmark } from '../components/brand/NexusBrand.jsx'
import { pickEnterTransitionSlogan } from '../utils/constants.js'

/** Fase 1: login → negro + «N S» dibujadas en paralelo */
const MORPH_MS = 2200
/** Pausa con «N S» + slogan visibles (tiempo para leer) */
const HOLD_MS = 1400
/** Fase 2: cortina → app */
const REVEAL_MS = 1000

/**
 * @typedef {'idle' | 'morph' | 'hold' | 'reveal'} TransitionPhase
 */

const AuthEnterTransitionContext = createContext(null)

export function AuthEnterTransitionProvider({ children }) {
  const location = useLocation()
  const [phase, setPhase] = useState(/** @type {TransitionPhase} */ ('idle'))
  const [enterSlogan, setEnterSlogan] = useState('')
  const timersRef = useRef(/** @type {number[]} */ ([]))

  const clearTimers = useCallback(() => {
    timersRef.current.forEach((id) => window.clearTimeout(id))
    timersRef.current = []
  }, [])

  useEffect(() => clearTimers, [clearTimers])

  const schedule = useCallback((fn, ms) => {
    const id = window.setTimeout(fn, ms)
    timersRef.current.push(id)
  }, [])

  /**
   * Inicia transición en dos pasos. `navigate` se llama al terminar la fase 1.
   * @param {() => void} navigate
   */
  const begin = useCallback(
    (navigate) => {
      clearTimers()
      document.documentElement.classList.add('nx-route-enter')
      setEnterSlogan(pickEnterTransitionSlogan())
      setPhase('morph')

      schedule(() => {
        navigate()
        setPhase('hold')
      }, MORPH_MS)

      schedule(() => {
        setPhase('reveal')
      }, MORPH_MS + HOLD_MS)

      schedule(() => {
        setPhase('idle')
        document.documentElement.classList.remove('nx-route-enter')
      }, MORPH_MS + HOLD_MS + REVEAL_MS)
    },
    [clearTimers, schedule],
  )

  const value = useMemo(
    () => ({
      phase,
      isMorphing: phase === 'morph',
      isActive: phase !== 'idle',
      begin,
    }),
    [phase, begin],
  )

  const showOverlay = phase !== 'idle'
  const showWaveBg =
    location.pathname === '/login' ||
    location.pathname === '/registro' ||
    phase !== 'idle'

  return (
    <AuthEnterTransitionContext.Provider value={value}>
      {showWaveBg ? (
        <div
          className={[
            'nx-login-starfield',
            phase === 'reveal' ? 'nx-login-starfield--out' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          aria-hidden
        >
          <LoginWaveBackground />
        </div>
      ) : null}
      {children}
      {showOverlay ? (
        <div
          className={[
            'nx-auth-curtain',
            phase === 'morph' ? 'nx-auth-curtain--morph' : '',
            phase === 'hold' ? 'nx-auth-curtain--hold' : '',
            phase === 'reveal' ? 'nx-auth-curtain--reveal' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          aria-hidden={phase === 'reveal'}
          aria-live="polite"
        >
          <TransitionNBurst phase={phase} />
          <p className="nx-auth-curtain__label">
            <NexusWordmark light showSlogan sloganText={enterSlogan} />
          </p>
        </div>
      ) : null}
    </AuthEnterTransitionContext.Provider>
  )
}

export function useAuthEnterTransition() {
  const ctx = useContext(AuthEnterTransitionContext)
  if (!ctx) {
    throw new Error('useAuthEnterTransition debe usarse dentro de AuthEnterTransitionProvider')
  }
  return ctx
}
