;(function () {
  if (window.__NEXUS_OUTREACH_EXTENSION__) return
  window.__NEXUS_OUTREACH_EXTENSION__ = true
  window.__NEXUS_WHATSAPP_EXTENSION__ = true

  window.nexusWhatsAppArmOpenChat = function nexusWhatsAppArmOpenChat(payload) {
    window.postMessage({ type: 'NEXUS_ARM_WHATSAPP_CHAT', payload: payload || {} }, window.location.origin)
  }

  window.nexusWhatsAppSetPending = function nexusWhatsAppSetPending(payload) {
    return new Promise(function (resolve, reject) {
      var id = 'nexus-wa-pending-' + Math.random().toString(36).slice(2)
      function onResponse(ev) {
        if (ev.source !== window || !ev.data || ev.data.type !== 'NEXUS_WHATSAPP_PENDING_RESPONSE' || ev.data.id !== id) {
          return
        }
        window.removeEventListener('message', onResponse)
        if (ev.data.ok) resolve(ev.data.result || { ok: true })
        else reject(new Error(ev.data.error || 'No se pudo registrar WhatsApp pendiente'))
      }
      window.addEventListener('message', onResponse)
      window.postMessage({ type: 'NEXUS_SET_WHATSAPP_PENDING', id: id, payload: payload || {} }, window.location.origin)
    })
  }

  window.nexusClearProspectWatch = function nexusClearProspectWatch(prospectId) {
    window.postMessage(
      { type: 'NEXUS_CLEAR_PROSPECT_WATCH', payload: { prospectId: Number(prospectId) || 0 } },
      window.location.origin,
    )
  }
})()
