import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { fetchCallAssistQueue, fetchCampaigns } from '../utils/api.js'
import { currentUserId } from '../utils/campaignUsers.js'
import { notifyQueueDesktopAlert } from '../utils/desktopNotifications.js'
import { campaignsOwnedByUser } from '../utils/myOwnedWork.js'

const POLL_MS = 30_000

let sharedPrevCount = /** @type {number | null} */ (null)

/**
 * Llamadas pendientes del SDR (cola asistida, todas sus campañas).
 */
export function useCallPending(companyId) {
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
      const campaigns = await fetchCampaigns(companyId)
      const list = campaignsOwnedByUser(campaigns, userId)
      let total = 0
      let best = null
      let bestN = 0

      await Promise.all(
        list.map(async (campaign) => {
          const queue = await fetchCallAssistQueue(campaign.id).catch(() => ({
            total_pending: 0,
          }))
          const n = Number(queue?.total_pending) || 0
          total += n
          if (n > bestN) {
            bestN = n
            best = campaign
          }
        }),
      )

      const fallback = list[0] || null
      const target = best || fallback
      const href = target ? `/campanas/${target.id}?focus=call` : '/campanas'

      const prev = sharedPrevCount ?? prevCountRef.current
      const isLoginSnapshot = prev === null
      const increased = prev !== null && total > prev

      if (total > 0 && isLoginSnapshot) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total,
          href,
          campaignName: target?.name,
          channel: 'call',
          reason: 'login',
        })
      } else if (increased) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total - prev,
          href,
          campaignName: target?.name,
          channel: 'call',
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
    window.addEventListener('nx:call-queue-changed', onChange)
    window.addEventListener('focus', onChange)
    const timer = window.setInterval(() => {
      void refresh()
    }, POLL_MS)
    return () => {
      window.removeEventListener('nx:call-queue-changed', onChange)
      window.removeEventListener('focus', onChange)
      window.clearInterval(timer)
    }
  }, [refresh])

  return { ...state, refresh }
}

/** @param {{ count?: number, href?: string, campaignName?: string }} [detail] */
export function notifyCallQueueChanged(detail) {
  window.dispatchEvent(new CustomEvent('nx:call-queue-changed', { detail: detail || {} }))
}
