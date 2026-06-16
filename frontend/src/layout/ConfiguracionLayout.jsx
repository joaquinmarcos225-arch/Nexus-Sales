import { NavLink, Outlet } from 'react-router-dom'
import { PageHeader } from './PageHeader'

const TABS = [
  { to: '/configuracion/integraciones', label: 'Integraciones' },
]

function tabClass({ isActive }) {
  return [
    'rounded-lg px-3 py-2 text-sm font-medium transition-colors',
    isActive
      ? 'bg-nx-brand text-white shadow-sm'
      : 'text-nx-muted hover:bg-nx-card-muted hover:text-nx-ink',
  ].join(' ')
}

export function ConfiguracionLayout() {
  return (
    <>
      <PageHeader
        title="Configuración"
        description="Conectá tus cuentas personales (Gmail, Calendar, WhatsApp, LinkedIn) para que Nexus opere desde tu usuario."
      />
      <nav className="mb-6 flex flex-wrap gap-2 border-b border-nx-border pb-4">
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} className={tabClass}>
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </>
  )
}
