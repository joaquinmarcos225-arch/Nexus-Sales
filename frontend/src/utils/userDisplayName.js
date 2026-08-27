const ROLE_LIKE = new Set([
  'sdr',
  'seller',
  'vendedor',
  'vendedora',
  'demo',
  'test',
  'director',
  'directora',
  'manager',
  'gerente',
  'admin',
])

function isRoleLike(word) {
  return ROLE_LIKE.has(String(word || '').trim().toLowerCase())
}

/** Primer nombre real para saludos (no rol ni parte del email). */
export function userDisplayFirstName(user) {
  const first = String(user?.first_name || '').trim()
  if (first && !isRoleLike(first.split(/\s+/)[0])) {
    return first.split(/\s+/)[0]
  }
  const full = String(user?.name || '').trim()
  if (full) {
    const part = full.split(/\s+/).find((w) => !isRoleLike(w))
    if (part) return part
  }
  return ''
}
