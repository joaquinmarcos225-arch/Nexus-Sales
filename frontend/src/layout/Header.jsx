import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import { ROLE_LABELS, normalizeRole } from '../data/navigation.js'
import { APP_NAME } from '../utils/constants'

/**
 * @param {{
 *   onMenuClick?: () => void,
 *   onToggleSidebar?: () => void,
 *   collapsed?: boolean,
 * }} props
 */
export function Header({ onMenuClick, onToggleSidebar, collapsed = false }) {
  const { companies, company } = useCompany()
  const { user, logout } = useAuth()
  const role = normalizeRole(user?.role)

  return (
    <header className="nx-topbar-gradient relative flex h-14 shrink-0 items-center justify-between px-4 lg:px-6">
      <div className="flex items-center gap-2 sm:gap-3">
        <button
          type="button"
          className="inline-flex rounded-lg p-2 text-white/80 hover:bg-white/10 lg:hidden"
          aria-label="Abrir menú"
          onClick={onMenuClick}
        >
          <svg
            className="size-5"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
            aria-hidden
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </button>
        <button
          type="button"
          className="hidden rounded-lg p-2 text-white/75 hover:bg-white/10 hover:text-white lg:inline-flex"
          aria-label={collapsed ? 'Expandir menú lateral' : 'Plegar menú lateral'}
          onClick={onToggleSidebar}
        >
          <svg
            viewBox="0 0 24 24"
            className={`size-5 transition-transform duration-300 ${collapsed ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <div className="hidden min-w-0 sm:block">
          <p className="truncate text-sm font-semibold text-white">{APP_NAME}</p>
          <p className="truncate text-[11px] text-white/55">Panel operativo</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {company ? (
          <span className="hidden max-w-[16rem] truncate rounded-full border border-white/15 bg-black/30 px-3 py-1 text-xs font-medium text-white/90 sm:inline">
            {company.name}
          </span>
        ) : null}
        {user ? (
          <div className="hidden items-center gap-2 sm:flex">
            <Link
              to="/mi-perfil"
              className="max-w-[12rem] truncate rounded-full border border-white/15 bg-black/30 px-3 py-1 text-xs text-white/90 hover:bg-black/40"
            >
              {user.first_name || user.name} · {ROLE_LABELS[role] || user.role}
            </Link>
            <button
              type="button"
              onClick={logout}
              className="rounded-lg border border-white/15 px-2 py-1 text-[11px] text-white/75 hover:bg-white/10"
            >
              Salir
            </button>
          </div>
        ) : null}
      </div>
    </header>
  )
}
