/**
 * Persistencia local del Asistente Nexus (por empresa).
 *
 * Migración futura a backend:
 * - Reemplazar load/save por GET/POST /companies/:id/assistant-history
 * - Mantener la misma forma `{ messages: [{ role, content }] }`
 * - Usar STORAGE_VERSION para migraciones de formato al cambiar API
 */

export const ASSISTANT_STORAGE_VERSION = 1
const KEY_PREFIX = 'nexus-assistant-chat'

function storageKey(companyId) {
  return `${KEY_PREFIX}:v${ASSISTANT_STORAGE_VERSION}:${companyId}`
}

/**
 * @returns {{ messages: Array<{ role: string, content: string }> }}
 */
export function loadAssistantThread(companyId) {
  if (companyId == null || typeof window === 'undefined') {
    return { messages: [] }
  }
  try {
    const raw = window.localStorage.getItem(storageKey(companyId))
    if (!raw) {
      return { messages: [] }
    }
    const data = JSON.parse(raw)
    const messages = Array.isArray(data.messages)
      ? data.messages
          .filter((m) => m && (m.role === 'user' || m.role === 'assistant'))
          .map((m) => ({
            role: m.role,
            content: String(m.content ?? '').trim(),
          }))
          .filter((m) => m.content.length > 0)
      : []
    return { messages }
  } catch {
    return { messages: [] }
  }
}

export function saveAssistantThread(companyId, messages) {
  if (companyId == null || typeof window === 'undefined') {
    return
  }
  try {
    const payload = JSON.stringify({
      companyId,
      version: ASSISTANT_STORAGE_VERSION,
      savedAt: new Date().toISOString(),
      messages: messages.map((m) => ({
        role: m.role,
        content: String(m.content ?? ''),
      })),
    })
    window.localStorage.setItem(storageKey(companyId), payload)
  } catch {
    // quota / private mode
  }
}

export function clearAssistantThread(companyId) {
  if (companyId == null || typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.removeItem(storageKey(companyId))
  } catch {
    // ignore
  }
}
