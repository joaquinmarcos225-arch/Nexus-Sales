import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'
import { isManagerOrGerente } from '../../data/navigation.js'

function tabClass({ isActive }) {
  return ['nx-tab shrink-0', isActive ? 'nx-tab-active' : ''].filter(Boolean).join(' ')
}

export default function ExplorarLayout() {
  const { user } = useAuth()
  const tabs = [
    { to: '/explorar', label: 'Buscar', end: true },
    { to: '/explorar/guardados', label: 'Guardados' },
  ]
  if (isManagerOrGerente(user)) {
    tabs.push({ to: '/explorar/pipeline', label: 'Base' })
  }
  return (
    <div className="space-y-6">
      <nav
        className="flex gap-1 overflow-x-auto border-b border-nx-border pb-px"
        aria-label="Secciones de explorar"
      >
        {tabs.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.end} className={tabClass}>
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
