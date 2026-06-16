import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { useSidebarCollapsed } from '../hooks/useSidebarCollapsed.js'
import { Header } from './Header'
import { Sidebar } from './Sidebar'

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { collapsed, toggle } = useSidebarCollapsed()

  return (
    <div className="nx-app-shell flex h-full min-h-0 overflow-hidden bg-nx-bg">
      {mobileOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-[#111827]/45 lg:hidden"
          aria-label="Cerrar menú"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}

      <div
        className={[
          'flex h-full min-h-0 shrink-0 flex-col',
          'fixed inset-y-0 left-0 z-50 transform transition-transform duration-300 ease-in-out',
          'lg:sticky lg:top-0 lg:z-auto lg:h-screen lg:translate-x-0',
          mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
        ].join(' ')}
      >
        <Sidebar
          collapsed={collapsed && !mobileOpen}
          onToggleCollapse={toggle}
          onNavigate={() => setMobileOpen(false)}
        />
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <Header
          collapsed={collapsed}
          onMenuClick={() => setMobileOpen(true)}
          onToggleSidebar={toggle}
        />
        <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4 lg:p-8">
          <div className="mx-auto max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
