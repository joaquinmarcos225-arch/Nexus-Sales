import { NavLink, Outlet } from 'react-router-dom'

const TABS = [
  { to: '/dashboard', label: 'Resumen', end: true },
  { to: '/dashboard/go-live', label: 'Go-live' },
]

function tabClass({ isActive }) {
  return ['nx-tab shrink-0', isActive ? 'nx-tab-active' : ''].filter(Boolean).join(' ')
}

export default function DashboardLayout() {
  return (
    <div className="space-y-6">
      <nav
        className="flex gap-1 overflow-x-auto border-b border-nx-border pb-px"
        aria-label="Secciones de consola"
      >
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.end} className={tabClass}>
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
