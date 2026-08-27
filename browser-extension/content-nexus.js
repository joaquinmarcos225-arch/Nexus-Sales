/**

 * Relay Nexus ↔ extensión: outbound assist, auth sync, inbound notifications.

 */

chrome.runtime.onMessage.addListener((message) => {

  if (message?.type === 'NEXUS_LI_WATCH_NEEDS_LINKEDIN') {
    window.postMessage({ type: 'NEXUS_LI_WATCH_NEEDS_LINKEDIN', payload: message }, '*')
  }


  if (message?.type === 'NEXUS_LINKEDIN_SENT_REGISTERED') {

    window.postMessage({ type: 'NEXUS_LINKEDIN_SENT_REGISTERED', payload: message }, '*')

  }

  if (message?.type === 'NEXUS_WHATSAPP_SENT_REGISTERED') {
    window.postMessage({ type: 'NEXUS_WHATSAPP_SENT_REGISTERED', payload: message }, '*')
  }

  if (message?.type === 'NEXUS_WHATSAPP_INBOUND_REGISTERED') {
    window.postMessage({ type: 'NEXUS_WHATSAPP_INBOUND_REGISTERED', payload: message }, '*')
  }

  if (message?.type === 'NEXUS_LINKEDIN_CONNECTION_REGISTERED') {

    window.postMessage({ type: 'NEXUS_LINKEDIN_CONNECTION_REGISTERED', payload: message }, '*')

  }

  if (message?.type === 'NEXUS_LINKEDIN_PROBE_DIAG') {
    window.postMessage({ type: 'NEXUS_LINKEDIN_PROBE_DIAG', payload: message }, '*')
  }

})



function syncAuthToExtension() {
  try {
    const token = localStorage.getItem('nexus_auth_token')
    const companyId = localStorage.getItem('nexus_sales_company_id')
    chrome.runtime.sendMessage({
      type: 'NEXUS_SYNC_AUTH',
      token,
      companyId: companyId ? Number(companyId) : null,
      apiBaseUrl: 'http://127.0.0.1:8002',
    })
  } catch {
    // extensión no instalada
  }
}

/** Despierta el SW y pide sondear checking (abrir perfil LinkedIn). */
function probePendingViaExtension() {
  try {
    chrome.runtime.sendMessage({ type: 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW' }, () => {
      void chrome.runtime.lastError
    })
  } catch {
    /* ignore */
  }
}

syncAuthToExtension()
// NO sondear al cargar/focus: la sección outreach dispara 1 probe.
// Si acá también abrimos → 2 pestañas LinkedIn.
setInterval(syncAuthToExtension, 15000)
window.addEventListener('storage', syncAuthToExtension)
window.addEventListener('focus', () => {
  syncAuthToExtension()
})



window.addEventListener('message', (event) => {
    if (event.source !== window) return
  const data = event.data
  if (!data?.type) return

  if (data.type === 'NEXUS_OPEN_EXTENSIONS_PAGE') {
    try {
      chrome.runtime.sendMessage({ type: 'NEXUS_OPEN_EXTENSIONS_PAGE' }, () => {
        void chrome.runtime.lastError
      })
    } catch {
      /* ignore */
    }
    return
  }

  if (data.type === 'NEXUS_SET_LINKEDIN_PENDING') {
    const { id, payload } = data
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_SET_LINKEDIN_PENDING',
          profileUrl: payload?.profileUrl,
          message: payload?.message,
          prospectId: payload?.prospectId,
          isReply: Boolean(payload?.isReply),
        },
        (response) => {
          const err = chrome.runtime.lastError
          if (err) {
            window.postMessage(
              { type: 'NEXUS_LINKEDIN_PENDING_RESPONSE', id, ok: false, error: err.message },
              '*',
            )
            return
          }
          if (!response?.ok) {
            window.postMessage(
              {
                type: 'NEXUS_LINKEDIN_PENDING_RESPONSE',
                id,
                ok: false,
                error: response?.error || 'No se pudo registrar envío pendiente',
              },
              '*',
            )
            return
          }
          window.postMessage(
            { type: 'NEXUS_LINKEDIN_PENDING_RESPONSE', id, ok: true, result: response },
            '*',
          )
        },
      )
    } catch (e) {
      window.postMessage(
        { type: 'NEXUS_LINKEDIN_PENDING_RESPONSE', id, ok: false, error: String(e?.message || e) },
        '*',
      )
    }
    return
  }

  if (data.type === 'NEXUS_LI_ARM_INBOUND_WATCH') {
    const { id, payload } = data
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_LI_ARM_INBOUND_WATCH',
          profileUrl: payload?.profileUrl,
          profileSlug: payload?.profileSlug,
          prospectId: payload?.prospectId,
          outboundText: payload?.outboundText || payload?.message || '',
          prospectName: payload?.prospectName || '',
        },
        (response) => {
          const err = chrome.runtime.lastError
          if (err) {
            window.postMessage(
              { type: 'NEXUS_LI_ARM_INBOUND_WATCH_RESPONSE', id, ok: false, error: err.message },
              '*',
            )
            return
          }
          window.postMessage(
            {
              type: 'NEXUS_LI_ARM_INBOUND_WATCH_RESPONSE',
              id,
              ok: Boolean(response?.ok),
              result: response || { ok: false },
              error: response?.error,
            },
            '*',
          )
        },
      )
    } catch (e) {
      window.postMessage(
        {
          type: 'NEXUS_LI_ARM_INBOUND_WATCH_RESPONSE',
          id,
          ok: false,
          error: String(e?.message || e),
        },
        '*',
      )
    }
    return
  }

  if (data.type === 'NEXUS_LI_POLL_INBOUND_NOW') {
    const { id, payload } = data
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_LI_POLL_INBOUND_NOW',
          profileUrl: payload?.profileUrl,
          profileSlug: payload?.profileSlug,
          prospectId: payload?.prospectId,
          outboundText: payload?.outboundText || payload?.message || '',
          prospectName: payload?.prospectName || '',
        },
        (response) => {
          const err = chrome.runtime.lastError
          if (err) {
            window.postMessage(
              {
                type: 'NEXUS_LI_POLL_INBOUND_NOW_RESPONSE',
                id,
                ok: false,
                result: { ok: false, reason: 'extension_error', error: err.message },
              },
              '*',
            )
            return
          }
          window.postMessage(
            {
              type: 'NEXUS_LI_POLL_INBOUND_NOW_RESPONSE',
              id,
              ok: Boolean(response?.ok || response?.candidates > 0),
              result: response || { ok: false },
            },
            '*',
          )
        },
      )
    } catch (e) {
      window.postMessage(
        {
          type: 'NEXUS_LI_POLL_INBOUND_NOW_RESPONSE',
          id,
          ok: false,
          result: { ok: false, error: String(e?.message || e) },
        },
        '*',
      )
    }
    return
  }

  if (data.type === 'NEXUS_RESOLVE_COMPOSE_URL') {
    const { id, payload } = data
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_RESOLVE_COMPOSE_URL',
          profileUrl: payload?.profileUrl,
        },
        (response) => {
          const err = chrome.runtime.lastError
          if (err) {
            window.postMessage(
              { type: 'NEXUS_RESOLVE_COMPOSE_RESPONSE', id, ok: false, error: err.message },
              '*',
            )
            return
          }
          window.postMessage(
            {
              type: 'NEXUS_RESOLVE_COMPOSE_RESPONSE',
              id,
              ok: Boolean(response?.ok || response?.composeUrl),
              result: response,
              error: response?.error,
            },
            '*',
          )
        },
      )
    } catch (e) {
      window.postMessage(
        { type: 'NEXUS_RESOLVE_COMPOSE_RESPONSE', id, ok: false, error: String(e?.message || e) },
        '*',
      )
    }
    return
  }

  if (data.type === 'NEXUS_ARM_OPEN_CHAT') {
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_ARM_OPEN_CHAT',
          profileUrl: data.payload?.profileUrl,
          prospectId: data.payload?.prospectId,
          message: data.payload?.message,
        },
        () => {
          /* fire-and-forget */
        },
      )
    } catch {
      /* ignore */
    }
    return
  }

  if (data.type === 'NEXUS_ARM_WHATSAPP_CHAT') {
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_ARM_WHATSAPP_CHAT',
          sendUrl: data.payload?.sendUrl,
          prospectId: data.payload?.prospectId,
          message: data.payload?.message,
          phoneDigits: data.payload?.phoneDigits,
          prospectName: data.payload?.prospectName,
        },
        () => {},
      )
    } catch {
      /* ignore */
    }
    return
  }

  if (data.type === 'NEXUS_SET_WHATSAPP_PENDING') {
    const { id, payload } = data
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_SET_WHATSAPP_PENDING',
          sendUrl: payload?.sendUrl,
          message: payload?.message,
          prospectId: payload?.prospectId,
          phoneDigits: payload?.phoneDigits,
        },
        (response) => {
          const err = chrome.runtime.lastError
          if (err) {
            window.postMessage(
              { type: 'NEXUS_WHATSAPP_PENDING_RESPONSE', id, ok: false, error: err.message },
              '*',
            )
            return
          }
          window.postMessage(
            {
              type: 'NEXUS_WHATSAPP_PENDING_RESPONSE',
              id,
              ok: Boolean(response?.ok),
              error: response?.error,
              result: response,
            },
            '*',
          )
        },
      )
    } catch (e) {
      window.postMessage(
        { type: 'NEXUS_WHATSAPP_PENDING_RESPONSE', id, ok: false, error: String(e?.message || e) },
        '*',
      )
    }
    return
  }

  if (data.type === 'NEXUS_CLEAR_PROSPECT_WATCH') {
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_CLEAR_PROSPECT_WATCH',
          prospectId: data.payload?.prospectId,
        },
        () => {
          void chrome.runtime.lastError
        },
      )
    } catch {
      /* ignore */
    }
    return
  }

  if (data.type === 'NEXUS_PROBE_LINKEDIN_CONNECTION') {
    const { id, payload } = data
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_PROBE_LINKEDIN_CONNECTION',
          profileUrl: payload?.profileUrl,
          prospectId: payload?.prospectId,
          prospectName: payload?.prospectName || payload?.name || undefined,
          connectionStatus: payload?.connectionStatus || payload?.mode || undefined,
        },
        (response) => {
          const err = chrome.runtime.lastError
          if (err) {
            window.postMessage(
              { type: 'NEXUS_PROBE_LINKEDIN_CONNECTION_RESPONSE', id, ok: false, error: err.message },
              '*',
            )
            return
          }
          window.postMessage(
            {
              type: 'NEXUS_PROBE_LINKEDIN_CONNECTION_RESPONSE',
              id,
              ok: Boolean(response?.ok),
              error: response?.error,
              result: response,
            },
            '*',
          )
        },
      )
    } catch (e) {
      window.postMessage(
        {
          type: 'NEXUS_PROBE_LINKEDIN_CONNECTION_RESPONSE',
          id,
          ok: false,
          error: String(e?.message || e),
        },
        '*',
      )
    }
    return
  }

  if (data.type === 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW') {
    const { id } = data
    try {
      chrome.runtime.sendMessage({ type: 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW' }, (response) => {
        const err = chrome.runtime.lastError
        if (err) {
          window.postMessage(
            {
              type: 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW_RESPONSE',
              id,
              ok: false,
              error: err.message,
            },
            '*',
          )
          return
        }
        window.postMessage(
          {
            type: 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW_RESPONSE',
            id,
            ok: Boolean(response?.ok),
            error: response?.error,
            result: response,
          },
          '*',
        )
      })
    } catch (e) {
      window.postMessage(
        {
          type: 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW_RESPONSE',
          id,
          ok: false,
          error: String(e?.message || e),
        },
        '*',
      )
    }
    return
  }

  if (data.type !== 'NEXUS_LINKEDIN_REQUEST') return

  const { id, payload } = data

  try {

    chrome.runtime.sendMessage(

      {

        type: 'NEXUS_OPEN_LINKEDIN',

        profileUrl: payload?.profileUrl,

        message: payload?.message,

        sessionId: payload?.sessionId,

        prospectId: payload?.prospectId,

        isReply: Boolean(payload?.isReply),

        adoptOnly: Boolean(payload?.adoptOnly),

        openChatOnly: Boolean(payload?.openChatOnly),

      },

      (response) => {

        const err = chrome.runtime.lastError

        if (err) {

          window.postMessage(

            { type: 'NEXUS_LINKEDIN_RESPONSE', id, ok: false, error: err.message },

            '*',

          )

          return

        }

        if (!response?.ok) {

          window.postMessage(

            {

              type: 'NEXUS_LINKEDIN_RESPONSE',

              id,

              ok: false,

              error: response?.error || 'La extensión no pudo abrir LinkedIn',

            },

            '*',

          )

          return

        }

        window.postMessage({ type: 'NEXUS_LINKEDIN_RESPONSE', id, ok: true, result: response }, '*')

      },

    )

  } catch (e) {

    window.postMessage(

      { type: 'NEXUS_LINKEDIN_RESPONSE', id, ok: false, error: String(e?.message || e) },

      '*',

    )

  }

})


