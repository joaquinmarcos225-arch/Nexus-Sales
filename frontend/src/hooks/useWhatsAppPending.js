import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { fetchCampaigns, fetchWhatsAppAssistQueue } from '../utils/api.js'
import { currentUserId } from '../utils/campaignUsers.js'
import { notifyQueueDesktopAlert } from '../utils/desktopNotifications.js'
import { campaignsOwnedByUser } from '../utils/myOwnedWork.js'

const POLL_MS = 30_000

/** Compartido entre instancias del hook para no duplicar alerts. */
let sharedPrevCount = /** @type {number | null} */ (null)

/**
 * Mensajes WhatsApp pendientes del usuario logueado (solo sus campañas como vendedor).
 * Desktop notify: solo al iniciar sesión o con la pestaña en segundo plano.
 */
export function useWhatsAppPending(companyId) {
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
          const queue = await fetchWhatsAppAssistQueue(campaign.id).catch(() => ({
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
      const href = target ? `/campanas/${target.id}?focus=whatsapp` : '/campanas'

      const prev = sharedPrevCount ?? prevCountRef.current
      const isLoginSnapshot = prev === null
      const increased = prev !== null && total > prev

      if (total > 0 && isLoginSnapshot) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total,
          href,
          campaignName: target?.name,
          channel: 'whatsapp',
          reason: 'login',
        })
      } else if (increased) {
        notifyQueueDesktopAlert({
          count: total,
          delta: total - prev,
          href,
          campaignName: target?.name,
          channel: 'whatsapp',
          reason: 'background',
        })
      }

      sharedPrevCount = total
      prevCountRef.current = total
      setState({
        count: total,
        href,
        loading: false,
      })
    } catch {
      setState((prev) => ({ ...prev, loading: false }))
    }
  }, [companyId, userId])

  useEffect(() => {
    void refresh()
    const onChange = () => {
      void refresh()
    }
    window.addEventListener('nx:whatsapp-queue-changed', onChange)
    window.addEventListener('nx:linkedin-queue-changed', onChange)
    window.addEventListener('focus', onChange)
    function onExtensionSent(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_WHATSAPP_SENT_REGISTERED') return
      onChange()
    }
    function onExtensionInbound(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_WHATSAPP_INBOUND_REGISTERED') return
      onChange()
    }
    window.addEventListener('message', onExtensionSent)
    window.addEventListener('message', onExtensionInbound)
    const timer = window.setInterval(() => {
      void refresh()
    }, POLL_MS)
    return () => {
      window.removeEventListener('nx:whatsapp-queue-changed', onChange)
      window.removeEventListener('nx:linkedin-queue-changed', onChange)
      window.removeEventListener('focus', onChange)
      window.removeEventListener('message', onExtensionSent)
      window.removeEventListener('message', onExtensionInbound)
      window.clearInterval(timer)
    }
  }, [refresh])

  return { ...state, refresh }
}

/**
 * @param {{ count?: number, href?: string, campaignName?: string }} [detail]
 */
export function notifyWhatsAppQueueChanged(detail) {
  window.dispatchEvent(new CustomEvent('nx:whatsapp-queue-changed', { detail: detail || {} }))
}
