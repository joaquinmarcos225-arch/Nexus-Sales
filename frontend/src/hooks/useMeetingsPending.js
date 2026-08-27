import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { fetchCampaigns, fetchCompanyMeetings } from '../utils/api.js'
import { currentUserId } from '../utils/campaignUsers.js'
import { notifyQueueDesktopAlert } from '../utils/desktopNotifications.js'
import { campaignsOwnedByUser, meetingsInCampaignIds } from '../utils/myOwnedWork.js'

const POLL_MS = 30_000
const RECENT_PAST_MS = 2 * 60 * 60 * 1000

/** Compartido entre instancias del hook para no duplicar alerts. */
let sharedPrevCount = /** @type {number | null} */ (null)

/**
 * Reuniones pendientes/confirmadas próximas (cola de “alguien agendó”).
 * @param {any[]} meetings
 * @returns {any[]}
 */
export function filterActionableMeetings(meetings) {
  const list = Array.isArray(meetings) ? meetings : []
  const cutoff = Date.now() - RECENT_PAST_MS
  return list
    .filter((m) => {
      const status = String(m?.meeting_status || '').toLowerCase()
      if (status !== 'pending' && status !== 'confirmed') return false
      const when = new Date(m.scheduled_for).getTime()
      return Number.isFinite(when) && when >= cutoff
    })
    .sort((a, b) => {
      const ta = new Date(a.scheduled_for).getTime()
      const tb = new Date(b.scheduled_for).getTime()
      return ta - tb
    })
}

/**
 * @param {any[]} meetings
 * @returns {number}
 */
export function countActionableMeetings(meetings) {
  return filterActionableMeetings(meetings).length
}

/**
 * Reuniones agendadas del usuario logueado (solo campañas donde es vendedor).
 * Mismo patrón que LinkedIn / WhatsApp.
 */
export function useMeetingsPending(companyId) {
  const { user } = useAuth()
  const userId = currentUserId(user)
  const [state, setState] = useState({ count: 0, href: '/campanas', loading: true })
  const prevCountRef = useRef(/** @type {number | null} */ (null))

  const refresh = useCallback(async () => {
    if (!companyId || !userId) {
      setState({ count: 0, href: '/campanas', loading: false })
      prevCountRef.current = null
      sharedPrevCount = null
      return
    }
    try {
      const [campaigns, meetings] = await Promise.all([
        fetchCampaigns(companyId).catch(() => []),
        fetchCompanyMeetings(companyId).catch(() => []),
      ])
      const mine = campaignsOwnedByUser(campaigns, userId)
      const myCampaignIds = new Set(mine.map((c) => Number(c.id)).filter((n) => n > 0))
      const actionable = filterActionableMeetings(meetingsInCampaignIds(meetings, myCampaignIds))
      const total = actionable.length

      /** @type {Map<number, { n: number, name: string }>} */
      const byCampaign = new Map()
      for (const m of actionable) {
        const cid = Number(m.campaign_id)
        if (!Number.isFinite(cid) || cid < 1) continue
        const cur = byCampaign.get(cid) || { n: 0, name: m.campaign_name || '' }
        cur.n += 1
        if (!cur.name && m.campaign_name) cur.name = m.campaign_name
        byCampaign.set(cid, cur)
      }

      let bestN = 0
      let bestName = ''
      for (const [, info] of byCampaign) {
        if (info.n > bestN) {
          bestN = info.n
          bestName = info.name
        }
      }

      const href = '/dashboard/reuniones'

      const prev = sharedPrevCount ?? prevCountRef.current
      const isLoginSnapshot = prev === null
      const increased = prev !== null && total > prev

      if (total > 0 && isLoginSnapshot) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total,
          href,
          campaignName: bestName || undefined,
          channel: 'meetings',
          reason: 'login',
        })
      } else if (increased) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total - prev,
          href,
          campaignName: bestName || undefined,
          channel: 'meetings',
          reason: 'background',
        })
      }

      sharedPrevCount = total
      prevCountRef.current = total
      setState({ count: total, href, loading: false })
    } catch {
      setState((prev) => ({ ...prev, loading: false }))
    }
  }, [companyId, userId])

  useEffect(() => {
    void refresh()
    const onChange = () => {
      void refresh()
    }
    window.addEventListener('nx:meetings-changed', onChange)
    window.addEventListener('focus', onChange)
    const timer = window.setInterval(() => {
      void refresh()
    }, POLL_MS)
    return () => {
      window.removeEventListener('nx:meetings-changed', onChange)
      window.removeEventListener('focus', onChange)
      window.clearInterval(timer)
    }
  }, [refresh])

  return { ...state, refresh }
}

/**
 * @param {{ count?: number, delta?: number, href?: string, campaignName?: string }} [detail]
 */
export function notifyMeetingsChanged(detail) {
  window.dispatchEvent(new CustomEvent('nx:meetings-changed', { detail: detail || {} }))
}
