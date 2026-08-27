import { useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import { ROLE_LABELS, normalizeRole } from '../data/navigation.js'
import { UserAvatar } from '../components/user/UserAvatar.jsx'
import { deleteMyAvatar, uploadMyAvatar } from '../utils/api.js'

export default function MiPerfilPage() {
  const { user, logout, refreshUser } = useAuth()
  const { company } = useCompany()
  const fileRef = useRef(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [avatarTick, setAvatarTick] = useState(0)

  if (!user) return null

  const role = normalizeRole(user.role)
  const displayName =
    `${user.first_name || ''} ${user.last_name || ''}`.trim() || user.name || 'Usuario'
  const companyName = user.company_name || company?.name || 'Tu empresa'

  async function onPickPhoto(ev) {
    const file = ev.target.files?.[0]
    ev.target.value = ''
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      await uploadMyAvatar(file)
      await refreshUser({ silent: true })
      setAvatarTick((n) => n + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function onRemovePhoto() {
    setBusy(true)
    setError(null)
    try {
      await deleteMyAvatar()
      await refreshUser({ silent: true })
      setAvatarTick((n) => n + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5 p-4 lg:p-6">
      <div>
        <h1 className="text-xl font-bold text-nx-ink">Mi perfil</h1>
        <p className="mt-1 text-sm text-nx-muted">
          Cómo te ven dentro de {companyName}. La foto es solo interna: no cambia los mensajes a
          prospectos.
        </p>
      </div>

      <section className="overflow-hidden rounded-2xl border border-nx-border bg-white shadow-sm">
        <div className="flex flex-col items-center gap-4 bg-gradient-to-br from-zinc-50 to-white px-6 py-8 sm:flex-row sm:items-start">
          <div className="relative">
            <UserAvatar
              name={displayName}
              avatarUrl={user.avatar_url}
              cacheKey={String(avatarTick)}
              size="lg"
              className="ring-4 ring-white shadow-md"
            />
          </div>
          <div className="min-w-0 flex-1 text-center sm:text-left">
            <h2 className="truncate text-lg font-semibold text-nx-ink">{displayName}</h2>
            <p className="mt-0.5 text-sm text-nx-muted">{ROLE_LABELS[role] || user.role}</p>
            <p className="mt-0.5 truncate text-sm text-nx-ink/80">{user.email}</p>
            <div className="mt-4 flex flex-wrap justify-center gap-2 sm:justify-start">
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="hidden"
                onChange={(ev) => void onPickPhoto(ev)}
              />
              <button
                type="button"
                disabled={busy}
                className="rounded-lg bg-nx-ink px-3 py-1.5 text-xs font-semibold text-white hover:bg-zinc-800 disabled:opacity-40"
                onClick={() => fileRef.current?.click()}
              >
                {busy ? 'Subiendo…' : user.avatar_url ? 'Cambiar foto' : 'Subir foto'}
              </button>
              {user.avatar_url ? (
                <button
                  type="button"
                  disabled={busy}
                  className="rounded-lg border border-nx-border px-3 py-1.5 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
                  onClick={() => void onRemovePhoto()}
                >
                  Quitar foto
                </button>
              ) : null}
            </div>
            {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
          </div>
        </div>

        <dl className="divide-y divide-nx-border border-t border-nx-border">
          <Row label="Empresa" value={companyName} />
          <Row label="Rol" value={ROLE_LABELS[role] || user.role} />
          <Row label="Email" value={user.email} />
        </dl>
      </section>

      <details className="rounded-xl border border-nx-border bg-white p-4">
        <summary className="cursor-pointer text-sm font-semibold text-nx-muted">Avanzado</summary>
        <p className="mt-2 text-xs text-nx-muted">
          Detalle técnico de permisos del rol (no hace falta mirarlo en el día a día).
        </p>
        <ul className="mt-2 max-h-40 list-inside list-disc text-[11px] text-nx-ink/80">
          {(user.permissions || []).map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </details>

      <button
        type="button"
        onClick={logout}
        className="rounded-lg border border-nx-border-strong px-4 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
      >
        Cerrar sesión
      </button>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-4 px-5 py-3.5 text-sm">
      <dt className="font-medium text-nx-muted">{label}</dt>
      <dd className="max-w-[70%] truncate text-right font-medium text-nx-ink">{value}</dd>
    </div>
  )
}
