import { normalizeRole, ROLES } from '../data/navigation.js'

export function currentUserId(user) {
  const raw = user?.user_id ?? user?.id
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? n : null
}

export function isCampaignAssignableUser(user) {
  if (!user) return false
  const role = normalizeRole(user.role)
  return (
    role === ROLES.sdr ||
    role === ROLES.manager ||
    role === ROLES.gerente ||
    role === ROLES.owner
  )
}

export function isDemoTestUser(user) {
  const email = String(user?.email || '').trim().toLowerCase()
  return (
    email === 'sdr@test.com' ||
    email === 'manager@test.com' ||
    email === 'director@test.com' ||
    email === 'owner@test.com'
  )
}

/** Quién puede recibir saldo asignado (pool → manager / peer SDR). */
export function isCreditEligibleUser(user) {
  if (!user) return false
  if (user.is_active === false) return false
  if (isDemoTestUser(user)) return false
  const role = normalizeRole(user.role)
  return role === ROLES.sdr || role === ROLES.manager
}

export function filterCampaignAssignableUsers(users) {
  return (users ?? []).filter(isCampaignAssignableUser)
}
