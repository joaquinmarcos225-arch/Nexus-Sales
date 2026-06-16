import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { APP_NAME } from '../utils/constants'

export default function LoginPage() {
  const { login, error: authError } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const from = location.state?.from || '/dashboard'

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(email.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo iniciar sesión')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-zinc-900 via-red-950 to-zinc-900 px-4">
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-white/95 p-8 shadow-2xl">
        <div className="mb-6 text-center">
          <p className="text-xs font-semibold uppercase tracking-wider text-red-800">{APP_NAME}</p>
          <h1 className="mt-1 text-2xl font-bold text-zinc-900">Iniciar sesión</h1>
          <p className="mt-1 text-sm text-zinc-600">Ingresá con tu email y contraseña de la empresa</p>
        </div>

        <form className="space-y-4" onSubmit={handleSubmit}>
          <label className="block text-sm font-medium text-zinc-800">
            Email
            <input
              type="email"
              autoComplete="username"
              required
              className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="juan@compa.demo"
            />
          </label>
          <label className="block text-sm font-medium text-zinc-800">
            Contraseña
            <input
              type="password"
              autoComplete="current-password"
              required
              className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {error || authError ? (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{error || authError}</p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-red-800 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-900 disabled:opacity-60"
          >
            {submitting ? 'Ingresando…' : 'Entrar'}
          </button>
        </form>

        <p className="mt-6 text-center text-[11px] text-zinc-500">
          Demo roles: sdr@test.com · manager@test.com · director@test.com
          <br />
          Contraseña: <span className="font-mono">demo123</span>
        </p>
      </div>
    </div>
  )
}
