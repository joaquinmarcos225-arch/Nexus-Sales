import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { isNavItemActive, sidebarNavForRole } from '../data/navigation'
import { APP_NAME } from '../utils/constants'
import { NavIcon } from './NavIcon.jsx'

function navClass({ isActive, collapsed }) {
  return [
    'group flex items-center rounded-lg text-sm font-medium transition-all duration-200',
    collapsed ? 'justify-center px-2 py-2.5' : 'gap-2.5 px-3 py-2',
    isActive
      ? 'bg-white/14 text-white shadow-sm shadow-black/25 ring-1 ring-white/10'
      : 'text-white/78 hover:bg-white/10 hover:text-white',
  ].join(' ')
}

export function Sidebar({ collapsed, onToggleCollapse, onNavigate }) {
  const { user } = useAuth()
  const location = useLocation()
  const navItems = sidebarNavForRole(user)

  return (
    <aside
      className={[
        'nx-sidebar-gradient relative flex h-full shrink-0 flex-col overflow-hidden transition-[width] duration-300 ease-in-out',
        collapsed ? 'w-[4.25rem]' : 'w-56 lg:w-60',
      ].join(' ')}
    >
      <SidebarBrand collapsed={collapsed} />

      <nav
        className={[
          'flex flex-1 flex-col gap-0.5 overflow-y-auto overflow-x-hidden',
          collapsed ? 'px-2 py-3' : 'p-3',
        ].join(' ')}
      >
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end === true && !item.activePrefix}
            title={collapsed ? item.label : undefined}
            className={() =>
              navClass({
                isActive: isNavItemActive(location.pathname, item),
                collapsed,
              })
            }
            onClick={() => onNavigate?.()}
          >
            <NavIcon name={item.icon} className="size-5 shrink-0 opacity-90" />
            {!collapsed ? <span className="truncate">{item.label}</span> : null}
          </NavLink>
        ))}
      </nav>

      <div className={['border-t border-white/10', collapsed ? 'px-2 py-2' : 'p-3'].join(' ')}>
        <button
          type="button"
          onClick={onToggleCollapse}
          title={collapsed ? 'Expandir menú' : 'Plegar menú'}
          className={[
            'flex w-full items-center rounded-lg text-white/70 transition-colors hover:bg-white/10 hover:text-white',
            collapsed ? 'justify-center p-2.5' : 'gap-2 px-3 py-2 text-xs font-medium',
          ].join(' ')}
        >
          <svg
            viewBox="0 0 24 24"
            className={`size-5 shrink-0 transition-transform duration-300 ${collapsed ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
          </svg>
          {!collapsed ? <span>Plegar menú</span> : null}
        </button>
      </div>
    </aside>
  )
}

function SidebarBrand({ collapsed }) {
  return (
    <div className="flex h-14 shrink-0 items-center border-b border-white/[0.06] px-3">
      <div
        className={[
          'flex min-w-0 items-center',
          collapsed ? 'w-full justify-center' : 'gap-2.5',
        ].join(' ')}
        title={APP_NAME}
      >
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-white to-white/85 text-sm font-bold text-red-800 shadow-md shadow-black/30 ring-1 ring-white/20">
          N
        </span>
        {!collapsed ? (
          <div className="min-w-0">
            <p className="truncate text-[15px] font-semibold leading-tight tracking-tight text-white">
              {APP_NAME}
            </p>
            <p className="truncate text-[10px] font-medium uppercase tracking-wider text-white/45">
              SDR OS
            </p>
          </div>
        ) : null}
      </div>
    </div>
  )
}
