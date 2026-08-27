/**
 * Permisos y navegación por rol — refleja backend/app/core/permissions.py
 */

export const ROLES = {
  sdr: 'sdr',
  manager: 'manager',
  gerente: 'gerente',
  owner: 'owner',
}

export const PERMISSIONS = {
  COMPANY_CONFIG: 'company.config',
  CONNECTIONS_OWN: 'connections.own',
  OPERATIONS_CONTROL: 'operations.control',
}

export const ROLE_LABELS = {
  sdr: 'SDR',
  manager: 'Manager',
  gerente: 'Director',
  owner: 'Owner',
}

/** Normaliza roles legacy del backend. */
export function normalizeRole(role) {
  const raw = String(role || '').trim().toLowerCase()
  if (raw === 'seller') return ROLES.sdr
  if (raw === 'admin' || raw === 'director') return ROLES.gerente
  if (
    raw === ROLES.sdr ||
    raw === ROLES.manager ||
    raw === ROLES.gerente ||
    raw === ROLES.owner
  ) {
    return raw
  }
  return role
}

export function hasPermission(user, permission) {
  if (!user?.permissions) return false
  return user.permissions.includes(permission)
}

/** Owner o Director — administración de empresa / pool. */
export function isCompanyAdmin(user) {
  if (!user) return false
  const role = normalizeRole(user.role)
  return role === ROLES.gerente || role === ROLES.owner
}

/** Gerente / Owner — productos y configuración global de empresa. */
export function canAccessCompanyConfig(user) {
  if (!user) return false
  if (hasPermission(user, PERMISSIONS.COMPANY_CONFIG)) return true
  return isCompanyAdmin(user)
}

/** Integraciones personales — todos los roles autenticados. */
export function canAccessPersonalIntegrations(user) {
  return Boolean(user)
}

/** Manager (centro de ops) o admin de empresa. */
export function isManagerOrGerente(user) {
  if (!user) return false
  const role = normalizeRole(user.role)
  return role === ROLES.manager || role === ROLES.gerente || role === ROLES.owner
}

/** Solo Manager ve Centro de operaciones (no director ni owner). */
export function canAccessOperations(user) {
  if (!user) return false
  if (hasPermission(user, PERMISSIONS.OPERATIONS_CONTROL)) {
    return normalizeRole(user.role) === ROLES.manager
  }
  return normalizeRole(user.role) === ROLES.manager
}

/** Flujo diario: Consola + Campañas (los contactos viven en el CRM del cliente). */
const BASE_NAV = [
  {
    type: 'link',
    to: '/dashboard',
    label: 'Consola',
    icon: 'resumen',
    activePrefix: '/dashboard',
  },
  { type: 'link', to: '/campanas', label: 'Campañas', icon: 'campanas' },
]

const TEAM_NAV = { type: 'link', to: '/equipo', label: 'Equipo', icon: 'equipo' }
const PRODUCTS_NAV = { type: 'link', to: '/productos', label: 'Productos/Servicios', icon: 'productos' }
const CONFIG_NAV = {
  type: 'link',
  to: '/configuracion/integraciones',
  label: 'Configuración',
  icon: 'config',
  activePrefix: '/configuracion',
}
const PROFILE_NAV = { type: 'link', to: '/mi-perfil', label: 'Mi Perfil', icon: 'perfil' }
const CREDITS_NAV = { type: 'link', to: '/creditos', label: 'Créditos', icon: 'creditos' }
const SUPPORT_NAV = { type: 'link', to: '/soporte', label: 'Soporte', icon: 'soporte' }

export function sidebarNavForRole(userOrRole) {
  const user = typeof userOrRole === 'object' && userOrRole !== null ? userOrRole : null
  const role = normalizeRole(user?.role)
  const items = [...BASE_NAV, TEAM_NAV]
  if (role === ROLES.gerente || role === ROLES.owner || role === ROLES.manager || role === ROLES.sdr) {
    items.push(CREDITS_NAV)
  }
  if (canAccessCompanyConfig(user ?? { role: user?.role, permissions: [] })) {
    items.push(PRODUCTS_NAV)
  }
  if (canAccessPersonalIntegrations(user)) {
    items.push(CONFIG_NAV)
  }
  items.push(SUPPORT_NAV)
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
