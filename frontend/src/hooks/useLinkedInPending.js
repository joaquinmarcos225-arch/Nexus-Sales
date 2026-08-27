import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { fetchCampaigns, fetchLinkedInAssistQueue } from '../utils/api.js'
import { currentUserId } from '../utils/campaignUsers.js'
import { notifyQueueDesktopAlert } from '../utils/desktopNotifications.js'
import { campaignsOwnedByUser } from '../utils/myOwnedWork.js'

const POLL_MS = 30_000

/** Compartido entre instancias del hook (Header + Dashboard) para no duplicar alerts. */
let sharedPrevCount = /** @type {number | null} */ (null)

/**
 * @param {any} queue
 * @returns {number}
 */
export function countLinkedInPending(queue) {
  const fromApi = Number(queue?.total_pending)
  if (Number.isFinite(fromApi) && fromApi >= 0) return fromApi
  const tasks = Array.isArray(queue?.tasks) ? queue.tasks : []
  return tasks.length
}

/**
 * Acciones LinkedIn pendientes del usuario logueado (solo sus campañas como vendedor).
 * `count` alimenta la campanita y "LinkedIn por enviar".
 * Desktop notify: solo al iniciar sesión o con la pestaña en segundo plano.
 */
export function useLinkedInPending(companyId) {
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
          const queue = await fetchLinkedInAssistQueue(campaign.id).catch(() => ({
            tasks: [],
            total_pending: 0,
          }))
          const n = countLinkedInPending(queue)
          total += n
          if (n > bestN) {
            bestN = n
            best = campaign
          }
        }),
      )

      const fallback = list[0] || null
      const target = best || fallback
      const href = target ? `/campanas/${target.id}?focus=linkedin` : '/campanas'

      const prev = sharedPrevCount ?? prevCountRef.current
      const isLoginSnapshot = prev === null
      const increased = prev !== null && total > prev

      if (total > 0 && isLoginSnapshot) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total,
          href,
          campaignName: target?.name,
          channel: 'linkedin',
          reason: 'login',
        })
      } else if (increased) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total - prev,
          href,
          campaignName: target?.name,
          channel: 'linkedin',
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
    window.addEventListener('nx:linkedin-queue-changed', onChange)
    window.addEventListener('focus', onChange)
    function onExtensionInbound(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_INBOUND_REGISTERED') return
      const payload = data.payload || {}
      if (payload.replyDelayed) {
        window.setTimeout(() => void refresh(), 130_000)
      }
      onChange()
    }
    function onExtensionSent(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_SENT_REGISTERED') return
      onChange()
    }
    window.addEventListener('message', onExtensionInbound)
    window.addEventListener('message', onExtensionSent)
    const timer = window.setInterval(() => {
      void refresh()
    }, POLL_MS)
    return () => {
      window.removeEventListener('nx:linkedin-queue-changed', onChange)
      window.removeEventListener('focus', onChange)
      window.removeEventListener('message', onExtensionInbound)
      window.removeEventListener('message', onExtensionSent)
      window.clearInterval(timer)
    }
  }, [refresh])

  return { ...state, refresh }
}

/**
 * @param {{ count?: number, delta?: number, href?: string, campaignName?: string }} [detail]
 */
export function notifyLinkedInQueueChanged(detail) {
  window.dispatchEvent(new CustomEvent('nx:linkedin-queue-changed', { detail: detail || {} }))
}
