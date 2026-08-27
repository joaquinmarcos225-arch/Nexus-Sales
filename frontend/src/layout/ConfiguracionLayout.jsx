import { NavLink, Outlet } from 'react-router-dom'
import { PageHeader } from './PageHeader'

const TAB_CLASS = ({ isActive }) => ['nx-tab', isActive ? 'nx-tab-active' : ''].filter(Boolean).join(' ')

export function ConfiguracionLayout() {
  return (
    <>
      <PageHeader
        kicker="Cuenta"
        title="Configuración"
        description="Instalá la extensión Chrome e integrá Gmail y Calendar. Cada persona configura sus propios canales."
      />

      <nav className="mb-6 flex gap-1 border-b border-nx-border/80 pb-px" aria-label="Secciones de configuración">
        <NavLink to="/configuracion/integraciones" end className={TAB_CLASS}>
          Mis canales
        </NavLink>
      </nav>

      <Outlet />
    </>
  )
}
