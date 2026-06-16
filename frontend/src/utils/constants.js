/** Nombre de la app (UI y futuras integraciones). */
export const APP_NAME = 'Nexus Sales'

const DEFAULT_API_BASE = 'http://127.0.0.1:8002'

/**
 * Limpia VITE_API_URL: espacios, BOM, comillas sueltas (muy habitual en .env mal editado).
 */
function normalizeApiBaseUrl(raw) {
  if (raw == null) {
    return DEFAULT_API_BASE
  }
  let s = String(raw).trim().replace(/^\uFEFF/, '')
  if (!s) {
    return DEFAULT_API_BASE
  }
  while (
    (s.startsWith('"') && s.endsWith('"')) ||
    (s.startsWith("'") && s.endsWith("'"))
  ) {
    s = s.slice(1, -1).trim()
  }
  while (s.endsWith('"') || s.endsWith("'")) {
    s = s.slice(0, -1).trim()
  }
  s = s.replace(/\/+$/, '')
  if (!s) {
    return DEFAULT_API_BASE
  }
  if (!/^https?:\/\//i.test(s)) {
    s = `http://${s.replace(/^\/+/, '')}`
  }
  return s
}

/**
 * URL del API en la red (siempre absoluta). Sirve al proxy de Vite y a mensajes de error.
 */
export const REMOTE_API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_URL)

/**
 * En `npm run dev`, por defecto las peticiones van al mismo origen (5173) y Vite hace proxy al REMOTE (evita CORS).
 * Desactivar: VITE_DEV_PROXY=0
 */
const devProxyOff =
  import.meta.env.VITE_DEV_PROXY === '0' ||
  import.meta.env.VITE_DEV_PROXY === 'false'
const useDevProxy = import.meta.env.DEV && !devProxyOff

/**
 * Base usada en fetch: vacía en dev+proxy (rutas relativas); si no, REMOTE_API_BASE_URL.
 */
export const API_BASE_URL = useDevProxy ? '' : REMOTE_API_BASE_URL

/**
 * Destino real del backend (para mensajes y depuración).
 */
export const API_EFFECTIVE_TARGET = REMOTE_API_BASE_URL

/**
 * URL que el navegador usará para un path del API (proxy relativo o base absoluta).
 */
export function resolveApiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`
  if (API_BASE_URL) {
    return `${API_BASE_URL.replace(/\/+$/, '')}${p}`
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    return `${window.location.origin}${p}`
  }
  return p
}
