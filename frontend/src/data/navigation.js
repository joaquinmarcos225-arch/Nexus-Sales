/**
 * Permisos y navegación por rol — refleja backend/app/core/permissions.py
 */

export const ROLES = {
  sdr: 'sdr',
  manager: 'manager',
  gerente: 'gerente',
}

export const PERMISSIONS = {
  COMPANY_CONFIG: 'company.config',
  CONNECTIONS_OWN: 'connections.own',
}

export const ROLE_LABELS = {
  sdr: 'SDR',
  manager: 'Manager',
  gerente: 'Gerente / Director',
}

/** Normaliza roles legacy del backend. */
export function normalizeRole(role) {
  const raw = String(role || '').trim().toLowerCase()
  if (raw === 'seller') return ROLES.sdr
  if (raw === 'admin' || raw === 'director') return ROLES.gerente
  if (raw === ROLES.sdr || raw === ROLES.manager || raw === ROLES.gerente) return raw
  return role
}

export function hasPermission(user, permission) {
  if (!user?.permissions) return false
  return user.permissions.includes(permission)
}

/** Gerente / Director — productos y configuración global de empresa. */
export function canAccessCompanyConfig(user) {
  if (!user) return false
  if (hasPermission(user, PERMISSIONS.COMPANY_CONFIG)) return true
  return normalizeRole(user.role) === ROLES.gerente
}

/** Integraciones personales — todos los roles autenticados. */
export function canAccessPersonalIntegrations(user) {
  return Boolean(user)
}

const BASE_NAV = [
  { type: 'link', to: '/dashboard', label: 'Inicio', icon: 'resumen', end: true },
  { type: 'link', to: '/campanas', label: 'Campañas', icon: 'campanas' },
  { type: 'link', to: '/dashboard/sourcing', label: 'Lead Sourcing', icon: 'sourcing' },
  { type: 'link', to: '/dashboard/outreach', label: 'Nexus Outreach', icon: 'outreach' },
  { type: 'link', to: '/prospectos', label: 'Prospectos', icon: 'prospectos' },
]

const TEAM_NAV = { type: 'link', to: '/equipo', label: 'Equipo', icon: 'equipo' }
const PRODUCTS_NAV = { type: 'link', to: '/productos', label: 'Productos', icon: 'productos' }
const CONFIG_NAV = {
  type: 'link',
  to: '/configuracion/integraciones',
  label: 'Configuración',
  icon: 'config',
  activePrefix: '/configuracion',
}
const PROFILE_NAV = { type: 'link', to: '/mi-perfil', label: 'Mi Perfil', icon: 'perfil' }

export function sidebarNavForRole(userOrRole) {
  const user = typeof userOrRole === 'object' && userOrRole !== null ? userOrRole : null
  const items = [...BASE_NAV, TEAM_NAV]
  if (canAccessCompanyConfig(user ?? { role: user?.role, permissions: [] })) {
    items.push(PRODUCTS_NAV)
  }
  if (canAccessPersonalIntegrations(user)) {
    items.push(CONFIG_NAV)
  }
  items.push(PROFILE_NAV)
  return items
}

/** @deprecated — usar sidebarNavForRole */
export const mainNav = BASE_NAV

export function isDashboardPath(pathname) {
  return pathname === '/dashboard' || pathname.startsWith('/dashboard/')
}

export function isDashboardChildActive(pathname, sub) {
  if (sub.end) {
    return pathname === sub.to
  }
  return pathname === sub.to || pathname.startsWith(`${sub.to}/`)
}

export function isNavItemActive(pathname, item) {
  if (item.activePrefix) {
    return pathname === item.activePrefix || pathname.startsWith(`${item.activePrefix}/`)
  }
  if (item.end === true) {
    return pathname === item.to
  }
  return pathname === item.to || pathname.startsWith(`${item.to}/`)
}
