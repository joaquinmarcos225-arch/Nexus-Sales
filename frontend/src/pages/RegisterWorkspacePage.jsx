import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { useAuthEnterTransition } from '../context/AuthEnterTransition.jsx'
import { Button } from '../components/ui/Button.jsx'
import { NexusBrandHero } from '../components/brand/NexusBrand.jsx'

export default function RegisterWorkspacePage() {
  const { registerWorkspace, error: authError, isAuthenticated } = useAuth()
  const { isMorphing, begin } = useAuthEnterTransition()
  const navigate = useNavigate()

  const [companyName, setCompanyName] = useState('')
  const [employeeCount, setEmployeeCount] = useState('10')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (isAuthenticated && !isMorphing) {
      navigate('/dashboard', { replace: true })
    }
  }, [isAuthenticated, isMorphing, navigate])

  async function handleSubmit(e) {
    e.preventDefault()
    if (isMorphing || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await registerWorkspace({
        company_name: companyName.trim(),
        employee_count: Number(employeeCount) || 0,
        plan: 'starter',
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        email: email.trim(),
        password,
      })
      begin(() => navigate('/dashboard', { replace: true }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo crear la empresa')
      setSubmitting(false)
    }
  }

  return (
    <div
      className={[
        'nx-login-scene relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10',
        isMorphing ? 'nx-login-scene--morphing' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <div className="nx-login-vignette pointer-events-none absolute inset-0" aria-hidden />

      <div className="nx-login-content relative z-10 w-full max-w-md">
        <div className="nx-login-hero mb-8">
          <NexusBrandHero />
          <h1 className="mt-6 text-center text-sm font-medium tracking-wide text-zinc-400">Registrar empresa</h1>
          <p className="mt-2 text-center text-xs text-zinc-500">
            Alta de workspace + cuenta directora. Sin datos demo.
          </p>
        </div>

        <div className="nx-login-card rounded-2xl p-7 sm:p-8">
          <form className="space-y-3" onSubmit={handleSubmit}>
            <label className="block text-sm font-medium text-nx-ink">
              Empresa
              <input
                required
                disabled={isMorphing}
                className="nx-input mt-1.5"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="Mi Empresa S.A."
              />
            </label>
            <label className="block text-sm font-medium text-nx-ink">
              Empleados
              <input
                inputMode="numeric"
                disabled={isMorphing}
                className="nx-input mt-1.5"
                value={employeeCount}
                onChange={(e) => setEmployeeCount(e.target.value)}
              />
            </label>
            <p className="text-[11px] text-nx-muted">
              El cupo de créditos se define en el acuerdo comercial (B2B), no al registrarse.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm font-medium text-nx-ink">
                Nombre
                <input
                  required
                  disabled={isMorphing}
                  className="nx-input mt-1.5"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                />
              </label>
              <label className="block text-sm font-medium text-nx-ink">
                Apellido
                <input
                  required
                  disabled={isMorphing}
                  className="nx-input mt-1.5"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                />
              </label>
            </div>
            <label className="block text-sm font-medium text-nx-ink">
              Email (directora)
              <input
                type="email"
                required
                autoComplete="username"
                disabled={isMorphing}
                className="nx-input mt-1.5"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <label className="block text-sm font-medium text-nx-ink">
              Contraseña
              <input
                type="password"
                required
                minLength={6}
                autoComplete="new-password"
                disabled={isMorphing}
                className="nx-input mt-1.5"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>

            {error || authError ? (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                {error || authError}
              </p>
            ) : null}

            <Button type="submit" disabled={submitting || isMorphing} className="w-full py-2.5 text-sm">
              {isMorphing ? 'Entrando…' : submitting ? 'Creando…' : 'Crear empresa y entrar'}
            </Button>
          </form>

          <p className="mt-6 border-t border-nx-border pt-5 text-center text-sm text-nx-muted">
            ¿Ya tenés cuenta?{' '}
            <Link to="/login" className="font-medium text-nx-ink underline">
              Iniciar sesión
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
