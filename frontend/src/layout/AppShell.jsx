import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { useMediaQuery } from '../hooks/useMediaQuery.js'
import { useSidebarCollapsed } from '../hooks/useSidebarCollapsed.js'
import { TutorialOverlay } from '../components/tutorial/TutorialOverlay.jsx'
import { ChromeWaveOverlay } from '../components/brand/ChromeWaveOverlay.jsx'
import { Header } from './Header'
import { Sidebar } from './Sidebar'

/** Celular + iPad: menú overlay. Desktop ancho: sidebar fija. */
const COMPACT_NAV = '(max-width: 1023px)'

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { collapsed, toggle } = useSidebarCollapsed()
  const compactNav = useMediaQuery(COMPACT_NAV)

  // En drawer (celular/iPad) siempre expandido con labels; no el rail de iconos.
  const effectiveCollapsed = compactNav ? false : collapsed
  const sidebarWidth = effectiveCollapsed ? 'w-14' : 'w-56'

  return (
    <div
      className={[
        'nx-app-shell relative flex h-full min-h-0 overflow-hidden',
        effectiveCollapsed ? 'nx-app-shell--sidebar-collapsed' : 'nx-app-shell--sidebar-expanded',
      ].join(' ')}
    >
      <div className="nx-chrome-l pointer-events-none absolute inset-0 z-0" aria-hidden />

      <div className="nx-chrome-top-waves pointer-events-none absolute inset-x-0 top-0 z-[5] h-14 overflow-hidden" aria-hidden>
        <ChromeWaveOverlay variant="topbar" />
      </div>

      {mobileOpen && compactNav ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-zinc-900/40 lg:hidden"
          aria-label="Cerrar menú"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}

      <div
        className={[
          'relative z-30 flex h-full min-h-0 shrink-0 flex-col overflow-hidden transition-[width] duration-300 ease-in-out',
          sidebarWidth,
          'fixed inset-y-0 left-0 z-50 transform lg:sticky lg:top-0 lg:z-30 lg:h-screen lg:translate-x-0',
          compactNav
            ? mobileOpen
              ? 'translate-x-0'
              : '-translate-x-full'
            : 'translate-x-0',
        ].join(' ')}
      >
        <Sidebar
          collapsed={effectiveCollapsed}
          onNavigate={() => setMobileOpen(false)}
        />
      </div>

      <div className="relative z-20 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <Header
          collapsed={effectiveCollapsed}
          compactNav={compactNav}
          onMenuClick={() => setMobileOpen(true)}
          onToggleSidebar={toggle}
        />
        <main className="nx-main-surface relative z-10 min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-3 pb-[max(1rem,env(safe-area-inset-bottom))] sm:p-5 lg:p-8">
          <div className="mx-auto w-full max-w-7xl">
            <Outlet />
          </div>
        </main>
      </div>
      <TutorialOverlay />
    </div>
  )
}
