;(function () {
  const ALLOWED_ORIGIN = window.location.origin
  const DEFAULT_API = 'https://api-production-21aa.up.railway.app'

  function syncAuthToExtension() {
    try {
      const token = localStorage.getItem('nexus_auth_token')
      const companyId = localStorage.getItem('nexus_sales_company_id')
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_SYNC_AUTH',
          token,
          companyId: companyId ? Number(companyId) : null,
          apiBaseUrl: DEFAULT_API,
        },
        () => void chrome.runtime.lastError,
      )
    } catch {
      /* extensión no disponible */
    }
  }

  window.addEventListener('message', (event) => {
    if (event.origin !== ALLOWED_ORIGIN) return
    if (event.source !== window) return
    const data = event.data
    if (!data || typeof data !== 'object') return

    if (data.type === 'NEXUS_ARM_WHATSAPP_CHAT') {
      chrome.runtime.sendMessage({ type: 'NEXUS_ARM_WHATSAPP_CHAT', ...(data.payload || {}) }, () => void chrome.runtime.lastError)
      return
    }

    if (data.type === 'NEXUS_CLEAR_PROSPECT_WATCH') {
      chrome.runtime.sendMessage(
        { type: 'NEXUS_CLEAR_PROSPECT_WATCH', prospectId: data.payload?.prospectId },
        () => void chrome.runtime.lastError,
      )
      return
    }

    if (data.type === 'NEXUS_SET_WHATSAPP_PENDING') {
      const id = data.id
      chrome.runtime.sendMessage({ type: 'NEXUS_SET_WHATSAPP_PENDING', ...(data.payload || {}) }, (response) => {
        const err = chrome.runtime.lastError
        window.postMessage(
          {
            type: 'NEXUS_WHATSAPP_PENDING_RESPONSE',
            id,
            ok: !err && Boolean(response?.ok),
            result: response || null,
            error: err?.message || response?.error || null,
          },
          ALLOWED_ORIGIN,
        )
      })
    }
  })

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || typeof message !== 'object') return
    if (message.type === 'NEXUS_WHATSAPP_SENT_REGISTERED') {
      window.postMessage(
        {
          type: 'NEXUS_WHATSAPP_SENT_REGISTERED',
          payload: { prospectId: message.prospectId || null },
          prospectId: message.prospectId || null,
        },
        ALLOWED_ORIGIN,
      )
    }
  })

  syncAuthToExtension()
  setInterval(syncAuthToExtension, 15000)
  window.addEventListener('storage', syncAuthToExtension)
  window.addEventListener('focus', syncAuthToExtension)
})()
