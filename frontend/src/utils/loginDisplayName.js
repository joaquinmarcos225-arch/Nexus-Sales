const STORAGE_KEY = 'nexus_login_display_names'

function readMap() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export function getStoredLoginDisplayName(email) {
  const key = String(email || '').trim().toLowerCase()
  if (!key) return ''
  return String(readMap()[key] || '').trim()
}

export function setStoredLoginDisplayName(email, displayName) {
  const key = String(email || '').trim().toLowerCase()
  const name = String(displayName || '').trim()
  if (!key || !name) return
  const map = readMap()
  map[key] = name
  localStorage.setItem(STORAGE_KEY, JSON.stringify(map))
}
