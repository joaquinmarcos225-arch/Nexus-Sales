import { Link } from 'react-router-dom'
import { useMyCredits } from '../hooks/useMyCredits.js'

export function HeaderCreditBadge() {
  const { available, showCredits, loading } = useMyCredits()

  if (!showCredits) {
    return null
  }

  const n = Number(available) || 0
  const label = n === 1 ? 'crédito' : 'créditos'

  return (
    <Link
      to="/creditos"
      className="nx-topbar-credits-badge inline-flex"
      title={`${n.toLocaleString('es-AR')} ${label} disponibles — ver Créditos de contacto`}
      aria-label={`${n.toLocaleString('es-AR')} ${label} disponibles`}
    >
      <svg className="size-4 shrink-0 opacity-80" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m3.75-3H20.25M3.75 6.75h16.5a1.5 1.5 0 011.5 1.5v9a1.5 1.5 0 01-1.5 1.5H3.75a1.5 1.5 0 01-1.5-1.5v-9a1.5 1.5 0 011.5-1.5z"
        />
      </svg>
      <span className="tabular-nums font-semibold">
        {loading ? '…' : n.toLocaleString('es-AR')}
      </span>
      <span className="hidden lg:inline text-zinc-400 font-normal">{label}</span>
    </Link>
  )
}
