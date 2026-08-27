import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { useAuthEnterTransition } from '../context/AuthEnterTransition.jsx'
import { Button } from '../components/ui/Button.jsx'
import { NexusBrandHero } from '../components/brand/NexusBrand.jsx'
import {
  getStoredLoginDisplayName,
  setStoredLoginDisplayName,
} from '../utils/loginDisplayName.js'

export default function LoginPage() {
  const { login, error: authError, isAuthenticated } = useAuth()
  const { isMorphing, begin } = useAuthEnterTransition()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [showDemoCreds, setShowDemoCreds] = useState(false)

  const from = location.state?.from || '/dashboard'
  const resetOk = Boolean(location.state?.passwordReset)

  useEffect(() => {
    if (isAuthenticated && !isMorphing) {
      navigate('/dashboard', { replace: true })
    }
  }, [isAuthenticated, isMorphing, navigate])

  function applyStoredNameForEmail(value) {
    const stored = getStoredLoginDisplayName(value)
    if (stored) {
      setDisplayName(stored)
    }
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (isMorphing || submitting) {
      return
    }
    const emailNorm = email.trim()
    const nameNorm = displayName.trim()
    if (!nameNorm) {
      setError('Ingresá tu nombre para firmar mensajes y personalizar la consola.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await login(emailNorm, password, nameNorm)
      setStoredLoginDisplayName(emailNorm, nameNorm)
      begin(() => navigate(from, { replace: true }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo iniciar sesión')
      setSubmitting(false)
    }
  }

  return (
    <div
      className={[
        'nx-login-scene relative flex h-dvh max-h-dvh items-start justify-center overflow-y-auto overflow-x-hidden px-4 py-4 sm:items-center sm:py-6',
        isMorphing ? 'nx-login-scene--morphing' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <div className="nx-login-vignette pointer-events-none absolute inset-0" aria-hidden />

      <div className="nx-login-content relative z-10 my-auto w-full max-w-[26rem] py-2 sm:max-w-[28rem]">
        <div className="nx-login-hero mb-5">
          <NexusBrandHero size="lg" />
          <h1 className="mt-3 text-center text-xs font-medium tracking-wide text-zinc-400 sm:text-[13px]">
            Iniciar sesión
          </h1>
        </div>

        <div className="nx-login-card rounded-2xl px-6 py-6 sm:px-8 sm:py-7">
          <form className="space-y-4" onSubmit={handleSubmit}>
            <label className="block text-sm font-medium text-nx-ink">
              Email
              <input
                type="email"
                autoComplete="username"
                required
                disabled={isMorphing}
                className="nx-input mt-1"
                value={email}
                onChange={(e) => {
                  const value = e.target.value
                  setEmail(value)
                  if (value.includes('@')) {
                    const stored = getStoredLoginDisplayName(value)
                    if (stored) setDisplayName(stored)
                  }
                }}
                onBlur={(e) => applyStoredNameForEmail(e.target.value)}
                placeholder="sdr@tuempresa.com"
              />
            </label>
            <label className="block text-sm font-medium text-nx-ink">
              Contraseña
              <input
                type="password"
                autoComplete="current-password"
                required
                disabled={isMorphing}
                className="nx-input mt-1"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <label className="block text-sm font-medium text-nx-ink">
              Tu nombre
              <input
                type="text"
                autoComplete="name"
                required
                disabled={isMorphing}
                className="nx-input mt-1"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Ej. María"
              />
            </label>

            {resetOk && !error && !authError ? (
              <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                Contraseña actualizada. Iniciá sesión con la nueva.
              </p>
            ) : null}

            {error || authError ? (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                {error || authError}
              </p>
            ) : null}

            <Button type="submit" disabled={submitting || isMorphing} className="w-full py-2.5 text-sm">
              {isMorphing ? 'Entrando…' : submitting ? 'Verificando…' : 'Entrar a Nexus'}
            </Button>
          </form>

          {import.meta.env.DEV ? (
            <p className="mt-4 border-t border-nx-border pt-3 text-center text-[11px] leading-relaxed text-nx-muted">
              <button
                type="button"
                className="font-medium text-nx-brand hover:underline"
                onClick={() => setShowDemoCreds((v) => !v)}
              >
                {showDemoCreds ? 'Ocultar credenciales demo' : 'Mostrar credenciales demo'}
              </button>
              {showDemoCreds ? (
                <>
                  <br />
                  <span className="font-mono text-nx-ink">manager@costguard.demo</span> ·{' '}
                  <span className="font-mono text-nx-ink">ana@costguard.demo</span> ·{' '}
                  <span className="font-mono text-nx-ink">admin@costguard.demo</span>
                  <br />
                  Contraseña: <span className="font-mono font-medium text-nx-ink">demo123</span>
                  <br />
                  <span className="text-[10px] text-nx-subtle">
                    Modo real local: las cuentas @test.com están desactivadas.
                  </span>
                </>
              ) : null}
            </p>
          ) : null}
          <p className="mt-2 text-center text-sm text-nx-muted">
            <Link to="/recuperar-contrasena" className="font-medium text-nx-ink underline">
              ¿Olvidaste tu contraseña?
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
