/**
 * Campañas cuyo vendedor asignado es el usuario (campanita / colas propias).
 * @param {any[]} campaigns
 * @param {number | null | undefined} userId
 */
export function campaignsOwnedByUser(campaigns, userId) {
  const uid = Number(userId)
  if (!Number.isFinite(uid) || uid < 1) return []
  const list = Array.isArray(campaigns) ? campaigns : []
  return list.filter((c) => Number(c?.seller_id) === uid)
}

/**
 * @param {any[]} meetings
 * @param {Iterable<number> | Set<number> | number[]} campaignIds
 */
export function meetingsInCampaignIds(meetings, campaignIds) {
  const allowed = campaignIds instanceof Set ? campaignIds : new Set(
    [...(campaignIds || [])].map(Number).filter((n) => Number.isFinite(n) && n > 0),
  )
  if (allowed.size === 0) return []
  const list = Array.isArray(meetings) ? meetings : []
  return list.filter((m) => allowed.has(Number(m?.campaign_id)))
}
