import { useMemo } from 'react'
import { useLocation } from 'react-router-dom'

const DASHBOARD_CHILD = {
  '/dashboard/go-live': 'Go-live',
}

const TOP_LEVEL = {
  '/dashboard': { section: 'Consola', sectionTo: '/dashboard', page: 'Resumen' },
  '/campanas': { section: 'Campañas' },
  '/equipo': { section: 'Equipo' },
  '/operaciones': { section: 'Operaciones' },
  '/creditos': { section: 'Créditos' },
  '/productos': { section: 'Productos/Servicios' },
  '/soporte': { section: 'Soporte' },
  '/configuracion/integraciones': { section: 'Configuración', page: 'Mis canales' },
  '/configuracion/idioma': { section: 'Configuración', page: 'Idioma' },
  '/mi-perfil': { section: 'Mi perfil' },
}

/**
 * Migas de pan según la ruta actual.
 * @returns {{ label: string, to?: string }[]}
 */
export function usePageBreadcrumbs() {
  const { pathname } = useLocation()

  return useMemo(() => {
    if (pathname.startsWith('/dashboard/')) {
      const child = DASHBOARD_CHILD[pathname]
      if (child) {
        return [
          { label: 'Consola', to: '/dashboard' },
          { label: child },
        ]
      }
    }

    if (pathname.startsWith('/campanas/')) {
      return [
        { label: 'Campañas', to: '/campanas' },
        { label: 'Detalle' },
      ]
    }

    if (pathname.startsWith('/configuracion')) {
      if (pathname.includes('/idioma')) {
        return [
          { label: 'Configuración', to: '/configuracion/integraciones' },
          { label: 'Idioma' },
        ]
      }
      return [
        { label: 'Configuración', to: '/configuracion/integraciones' },
        { label: 'Mis canales' },
      ]
    }

    const top = TOP_LEVEL[pathname]
    if (top) {
      if (top.page) {
        return [
          { label: top.section, to: top.sectionTo || pathname },
          { label: top.page },
        ]
      }
      return [{ label: top.section }]
    }

    return [{ label: 'Nexus' }]
  }, [pathname])
}
