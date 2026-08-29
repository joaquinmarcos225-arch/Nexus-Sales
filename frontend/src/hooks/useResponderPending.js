import { useCallback, useEffect, useState } from 'react'
import { fetchResponderInbox } from '../utils/api.js'

const POLL_MS = 45_000

export function useResponderPending(companyId) {
  const [state, setState] = useState({ count: 0, href: '/dashboard/responder', loading: true })

  const refresh = useCallback(async () => {
    if (!companyId) {
      setState({ count: 0, href: '/dashboard/responder', loading: false })
      return
    }
    try {
      const data = await fetchResponderInbox(companyId)
      const total = Number(data?.total) || 0
      setState({ count: total, href: '/dashboard/responder', loading: false })
    } catch {
      setState((prev) => ({ ...prev, loading: false }))
    }
  }, [companyId])

  useEffect(() => {
    void refresh()
    const onChange = () => void refresh()
    window.addEventListener('nx:responder-inbox-changed', onChange)
    window.addEventListener('focus', onChange)
    const timer = window.setInterval(() => void refresh(), POLL_MS)
    return () => {
      window.removeEventListener('nx:responder-inbox-changed', onChange)
      window.removeEventListener('focus', onChange)
      window.clearInterval(timer)
    }
  }, [refresh])

  return { ...state, refresh }
}

export function notifyResponderInboxChanged() {
  window.dispatchEvent(new CustomEvent('nx:responder-inbox-changed'))
}
