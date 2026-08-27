/** Nombre de la app (UI y futuras integraciones). */
export const APP_NAME = 'Nexus Sales'

/** Slogans rotativos — transición post-login (N + S). */
export const APP_SLOGANS = [
  'Tu prospección multicanal. Inicia sesión y conquista tu día',
  'Listo para llenar tu agenda de reuniones?',
  'Automatiza tus contactos, multiplica tus ventas.',
  'Hoy no perseguís leads: los leads te encuentran a vos.',
  'Menos cold calls eternos. Más reuniones que cierran.',
  'Tu pipeline no duerme. Vos tampoco tenés que perseguirlo.',
  'De primer contacto a reunión: Nexus hace el camino.',
  'Prospección sin fricción. Resultados con ritmo.',
  'Cada mensaje cuenta. Cada secuencia suma.',
  'Entrá, activá y que tu agenda se llene sola.',
  'El follow-up perfecto, sin que lo tengas que recordar.',
  'Vendé más hablando menos con quien no responde.',
  'Tu equipo de outreach, siempre encendido.',
  'Convertí silencios en conversaciones. Conversaciones en citas.',
  'La disciplina de prospectar, ahora en automático.',
  'Menos tabs abiertos. Más deals en movimiento.',
  'Hoy es un buen día para llenar el calendario.',
  'Secuencias listas. Prospectos calientes. Vos al mando.',
  'Que la IA trabaje de noche. Vos cerrás de día.',
  'Un clic. Un flujo. Muchas reuniones por delante.',
]

/** @deprecated Preferí pickAppSlogan() o APP_SLOGANS */
export const APP_SLOGAN = APP_SLOGANS[0]

/**
 * Slogan de la transición post-login: rota entre todas las frases en cada entrada.
 * @returns {string}
 */
export function pickEnterTransitionSlogan() {
  if (typeof window === 'undefined') return APP_SLOGANS[0]
  try {
    const key = 'nexus_enter_slogan_idx'
    let idx = Number(sessionStorage.getItem(key))
    if (!Number.isFinite(idx) || idx < 0 || idx >= APP_SLOGANS.length) {
      idx = 0
    }
    const slogan = APP_SLOGANS[idx]
    sessionStorage.setItem(key, String((idx + 1) % APP_SLOGANS.length))
    return slogan
  } catch {
    return APP_SLOGANS[Math.floor(Math.random() * APP_SLOGANS.length)]
  }
}

/**
 * Elige un slogan estable por sesión (cambia al recargar).
 * @returns {string}
 */
export function pickAppSlogan() {
  if (typeof window === 'undefined') return APP_SLOGANS[0]
  try {
    const ver = 'v4'
    const key = 'nexus_slogan_idx'
    const verKey = 'nexus_slogan_ver'
    if (sessionStorage.getItem(verKey) !== ver) {
      sessionStorage.setItem(verKey, ver)
      sessionStorage.removeItem(key)
    }
    let idx = Number(sessionStorage.getItem(key))
    if (!Number.isFinite(idx) || idx < 0 || idx >= APP_SLOGANS.length) {
      idx = Math.floor(Math.random() * APP_SLOGANS.length)
      sessionStorage.setItem(key, String(idx))
    }
    return APP_SLOGANS[idx]
  } catch {
    return APP_SLOGANS[Math.floor(Math.random() * APP_SLOGANS.length)]
  }
}

const DEFAULT_API_BASE = 'http://127.0.0.1:8002'

const SAME_ORIGIN_TOKENS = new Set(['same', 'relative', 'same-origin', '/', '.'])

/**
 * Limpia VITE_API_URL: espacios, BOM, comillas sueltas (muy habitual en .env mal editado).
 * `same` / `relative` → mismo origen (nginx proxy en Docker/prod).
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
  if (SAME_ORIGIN_TOKENS.has(s.toLowerCase())) {
    return ''
  }
  if (!/^https?:\/\//i.test(s)) {
    s = `http://${s.replace(/^\/+/, '')}`
  }
  return s
}

/**
 * URL del API en la red (absoluta, o '' = mismo origen).
 */
export const REMOTE_API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_URL)

/**
 * En `npm run dev`, por defecto las peticiones van al mismo origen (5173) y Vite hace proxy al REMOTE (evita CORS).
 * Desactivar: VITE_DEV_PROXY=0
 * En prod/Docker con VITE_API_URL=same también usa mismo origen.
 */
const devProxyOff =
  import.meta.env.VITE_DEV_PROXY === '0' ||
  import.meta.env.VITE_DEV_PROXY === 'false'
const useDevProxy = import.meta.env.DEV && !devProxyOff
const useSameOrigin = !REMOTE_API_BASE_URL

/**
 * Base usada en fetch: vacía en dev+proxy o same-origin; si no, REMOTE_API_BASE_URL.
 */
export const API_BASE_URL = useDevProxy || useSameOrigin ? '' : REMOTE_API_BASE_URL

/**
 * Destino real del backend (para mensajes y depuración).
 */
export const API_EFFECTIVE_TARGET = REMOTE_API_BASE_URL || '(mismo origen)'

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
