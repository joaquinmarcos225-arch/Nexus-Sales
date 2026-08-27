import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button } from '../components/ui/Button.jsx'
import { NexusBrandHero } from '../components/brand/NexusBrand.jsx'
import {
  confirmPasswordReset,
  requestPasswordReset,
  verifyPasswordResetCode,
} from '../utils/api.js'

export default function ForgotPasswordPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [info, setInfo] = useState(null)
  const [devCode, setDevCode] = useState(null)

  async function handleRequest(e) {
    e.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    setInfo(null)
    setDevCode(null)
    try {
      const res = await requestPasswordReset(email.trim())
      if (res?.dev_code) setDevCode(String(res.dev_code))
      setInfo(`Te enviamos un código a ${res?.email || email.trim()}.`)
      setStep('code')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo enviar el código')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleVerify(e) {
    e.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await verifyPasswordResetCode(email.trim(), code.trim())
      setInfo('Código correcto. Elegí tu nueva contraseña.')
      setStep('password')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'El código no es válido')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleConfirm(e) {
    e.preventDefault()
    if (submitting) return
    if (password !== passwordConfirm) {
      setError('Las contraseñas no coinciden.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await confirmPasswordReset(email.trim(), code.trim(), password, passwordConfirm)
      navigate('/login', { replace: true, state: { passwordReset: true } })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo cambiar la contraseña')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="nx-login-scene relative flex h-dvh max-h-dvh items-start justify-center overflow-y-auto overflow-x-hidden px-4 py-4 sm:items-center sm:py-6">
      <div className="nx-login-vignette pointer-events-none absolute inset-0" aria-hidden />
      <div className="nx-login-content relative z-10 my-auto w-full max-w-[26rem] py-2 sm:max-w-[28rem]">
        <div className="nx-login-hero mb-5">
          <NexusBrandHero size="lg" />
          <h1 className="mt-3 text-center text-xs font-medium tracking-wide text-zinc-400 sm:text-[13px]">
            Recuperar contraseña
          </h1>
        </div>

        <div className="nx-login-card rounded-2xl px-6 py-6 sm:px-8 sm:py-7">
          {step === 'email' ? (
            <form className="space-y-4" onSubmit={handleRequest}>
              <p className="text-sm leading-relaxed text-nx-muted">
                Ingresá el email de tu usuario Nexus. Si existe, te mandamos un código para
                elegir una nueva contraseña.
              </p>
              <label className="block text-sm font-medium text-nx-ink">
                Email
                <input
                  type="email"
                  autoComplete="username"
                  required
                  className="nx-input mt-1"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="sdr@tuempresa.com"
                />
              </label>
              {error ? (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                  {error}
                </p>
              ) : null}
              <Button type="submit" disabled={submitting} className="w-full py-2.5 text-sm">
                {submitting ? 'Enviando…' : 'Enviar código'}
              </Button>
            </form>
          ) : null}

          {step === 'code' ? (
            <form className="space-y-4" onSubmit={handleVerify}>
              {info ? <p className="text-sm leading-relaxed text-nx-ink">{info}</p> : null}
              {devCode ? (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                  Modo local sin SMTP. Código: <span className="font-mono font-semibold">{devCode}</span>
                </p>
              ) : null}
              <label className="block text-sm font-medium text-nx-ink">
                Código
                <input
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                  className="nx-input mt-1 tracking-[0.3em]"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="000000"
                />
              </label>
              {error ? (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                  {error}
                </p>
              ) : null}
              <Button type="submit" disabled={submitting} className="w-full py-2.5 text-sm">
                {submitting ? 'Verificando…' : 'Continuar'}
              </Button>
              <button
                type="button"
                className="w-full text-center text-sm text-nx-muted hover:underline"
                onClick={() => {
                  setStep('email')
                  setCode('')
                  setError(null)
                  setInfo(null)
                }}
              >
                Usar otro email
              </button>
            </form>
          ) : null}

          {step === 'password' ? (
            <form className="space-y-4" onSubmit={handleConfirm}>
              {info ? <p className="text-sm leading-relaxed text-nx-ink">{info}</p> : null}
              <label className="block text-sm font-medium text-nx-ink">
                Nueva contraseña
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  className="nx-input mt-1"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <label className="block text-sm font-medium text-nx-ink">
                Repetir contraseña
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  className="nx-input mt-1"
                  value={passwordConfirm}
                  onChange={(e) => setPasswordConfirm(e.target.value)}
                />
              </label>
              {error ? (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                  {error}
                </p>
              ) : null}
              <Button type="submit" disabled={submitting} className="w-full py-2.5 text-sm">
                {submitting ? 'Guardando…' : 'Cambiar contraseña'}
              </Button>
            </form>
          ) : null}

          <p className="mt-4 text-center text-sm text-nx-muted">
            <Link to="/login" className="font-medium text-nx-ink underline">
              Volver al inicio de sesión
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
