import { useAuth } from '../context/AuthContext.jsx'
import { ROLE_LABELS, normalizeRole } from '../data/navigation.js'

export default function MiPerfilPage() {
  const { user, logout } = useAuth()
  if (!user) return null

  const role = normalizeRole(user.role)

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4 lg:p-6">
      <h1 className="text-xl font-bold text-zinc-900">Mi perfil</h1>
      <dl className="divide-y divide-zinc-200 rounded-xl border border-zinc-200 bg-white">
        <Row label="Nombre" value={`${user.first_name} ${user.last_name}`.trim() || user.name} />
        <Row label="Email" value={user.email} />
        <Row label="Rol" value={ROLE_LABELS[role] || user.role} />
        <Row label="Empresa ID" value={String(user.company_id)} />
        <Row label="Usuario ID" value={String(user.user_id)} />
      </dl>

      <details className="rounded-xl border border-zinc-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-semibold text-zinc-800">
          Permisos ({user.permissions?.length || 0})
        </summary>
        <ul className="mt-2 max-h-48 list-inside list-disc text-xs text-zinc-700">
          {(user.permissions || []).map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </details>

      <button
        type="button"
        onClick={logout}
        className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-800 hover:bg-zinc-50"
      >
        Cerrar sesión
      </button>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-4 px-4 py-3 text-sm">
      <dt className="font-medium text-zinc-600">{label}</dt>
      <dd className="text-right text-zinc-900">{value}</dd>
    </div>
  )
}
