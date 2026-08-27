import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { tourIdFromNavIcon } from '../data/tutorialSteps.js'
import { isNavItemActive, sidebarNavForRole, ROLE_LABELS, normalizeRole } from '../data/navigation'
import { APP_NAME } from '../utils/constants'
import { NavIcon } from './NavIcon.jsx'
import { NexusLogoMark } from '../components/brand/NexusBrand.jsx'
import { ChromeWaveOverlay } from '../components/brand/ChromeWaveOverlay.jsx'
import { UserAvatar } from '../components/user/UserAvatar.jsx'

const SECTION_ORDER = [
  { title: 'Principal', labels: ['Consola', 'Campañas'] },
  { title: 'Gestión', labels: ['Equipo', 'Créditos', 'Productos/Servicios'] },
  { title: 'Sistema', labels: ['Configuración', 'Soporte'] },
  { title: null, labels: ['Mi Perfil'] },
]

function groupNavItems(items) {
  const used = new Set()
  const sections = []

  for (const section of SECTION_ORDER) {
    const sectionItems = items.filter((item) => section.labels.includes(item.label))
    if (sectionItems.length === 0) {
      continue
    }
    sectionItems.forEach((item) => used.add(item.label))
    sections.push({ title: section.title, items: sectionItems })
  }

  const rest = items.filter((item) => !used.has(item.label))
  if (rest.length > 0) {
    sections.push({ title: null, items: rest })
  }

  return sections
}

function navClass({ isActive, collapsed }) {
  return [
    'group nx-nav-link flex items-center rounded-lg text-[14px] font-normal tracking-wide transition-colors duration-200',
    collapsed ? 'justify-center px-1.5 py-2.5' : 'gap-2.5 px-2.5 py-2.5',
    isActive ? 'nx-nav-active' : 'text-zinc-100 hover:bg-white/[0.1] hover:text-white',
  ].join(' ')
}

/**
 * @param {{
 *   collapsed: boolean,
 *   onNavigate?: () => void,
 * }} props
 */
export function Sidebar({ collapsed, onNavigate }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const navItems = sidebarNavForRole(user)
  const sections = groupNavItems(navItems)
  const role = normalizeRole(user?.role)
  const displayName = user?.first_name || user?.name || 'Usuario'

  function handleLogout() {
    onNavigate?.()
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <aside
      className={[
        'nx-sidebar-shell nx-chrome-surface relative flex h-full shrink-0 flex-col overflow-hidden transition-[width] duration-300 ease-in-out',
        collapsed ? 'w-14' : 'w-56',
      ].join(' ')}
    >
      <div className="pointer-events-none absolute inset-x-0 bottom-0 top-14 overflow-hidden" aria-hidden>
        <ChromeWaveOverlay variant="sidebar" />
      </div>
      <div className="relative z-10 flex min-h-0 flex-1 flex-col text-zinc-100">
      <Link
        to="/dashboard"
        className="nx-sidebar-brand flex h-14 shrink-0 items-center justify-center px-2 outline-none transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:ring-nx-brand/50"
        aria-label={`${APP_NAME} — ir a Consola`}
        title="Consola"
      >
        <NexusLogoMark size={collapsed ? 36 : 50} className="shrink-0" title={APP_NAME} />
      </Link>

      <nav
        className={[
          'flex flex-1 flex-col gap-3 overflow-y-auto overflow-x-hidden',
          collapsed ? 'px-1.5 py-2' : 'p-2.5',
        ].join(' ')}
        aria-label="Navegación principal"
      >
        {sections.map((section) => (
          <div key={section.title || 'misc'} className="space-y-0.5">
            {section.title && !collapsed ? (
              <p className="px-3 pb-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-red-200/90">
                {section.title}
              </p>
            ) : null}
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end === true && !item.activePrefix}
                title={collapsed ? item.label : undefined}
                data-tour={tourIdFromNavIcon(item.icon) || undefined}
                className={() =>
                  navClass({
                    isActive: isNavItemActive(location.pathname, item),
                    collapsed,
                  })
                }
                onClick={() => onNavigate?.()}
              >
                <NavIcon
                  name={item.icon}
                  className="size-5 shrink-0 text-zinc-100 opacity-100 transition-colors duration-200 group-hover:text-red-300"
                />
                {!collapsed ? <span className="truncate">{item.label}</span> : null}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className={['border-t border-white/[0.06]', collapsed ? 'space-y-1 px-1.5 py-2' : 'space-y-1.5 p-2.5'].join(' ')}>
        {!collapsed && user ? (
          <Link
            to="/mi-perfil"
            onClick={() => onNavigate?.()}
            className="flex items-center gap-2.5 rounded-lg px-2 py-2 transition hover:bg-white/[0.06]"
          >
            <UserAvatar
              name={displayName}
              avatarUrl={user.avatar_url}
              size="sm"
              className="ring-1 ring-white/10"
            />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-zinc-100">{displayName}</p>
              <p className="truncate text-[11px] text-zinc-500">{ROLE_LABELS[role] || user.role}</p>
            </div>
          </Link>
        ) : null}

        <button
          type="button"
          onClick={handleLogout}
          title="Cerrar sesión"
          className={[
            'nx-nav-link flex w-full items-center rounded-lg text-zinc-500 transition-all duration-200 hover:bg-[#18181b] hover:text-red-400',
            collapsed ? 'justify-center p-2.5' : 'gap-2 px-3 py-2 text-xs font-medium',
          ].join(' ')}
        >
          <svg className="size-5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9"
            />
          </svg>
          {!collapsed ? <span>Cerrar sesión</span> : null}
        </button>
      </div>
      </div>
    </aside>
  )
}
