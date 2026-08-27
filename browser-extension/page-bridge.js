(function () {
  if (window.__NEXUS_LINKEDIN_EXTENSION__) return
  window.__NEXUS_LINKEDIN_EXTENSION__ = true

  /** Resuelve URL de compose para abrir el chat directo. */
  window.nexusLinkedInResolveCompose = function nexusLinkedInResolveCompose(payload) {
    return new Promise(function (resolve) {
      var id = 'nexus-li-compose-' + Math.random().toString(36).slice(2)
      var done = false
      function finish(result) {
        if (done) return
        done = true
        window.clearTimeout(timer)
        window.removeEventListener('message', onResponse)
        resolve(result || {})
      }
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_RESOLVE_COMPOSE_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        finish(ev.data.result || { composeUrl: null, error: ev.data.error })
      }
      window.addEventListener('message', onResponse)
      var timer = window.setTimeout(function () {
        finish({ composeUrl: null, method: 'timeout' })
      }, 10000)
      window.postMessage({ type: 'NEXUS_RESOLVE_COMPOSE_URL', id: id, payload: payload || {} }, '*')
    })
  }

  /** Dispara abrir chat en la pestaña de LinkedIn ya abierta (fire-and-forget). */
  window.nexusLinkedInArmOpenChat = function nexusLinkedInArmOpenChat(payload) {
    window.postMessage({ type: 'NEXUS_ARM_OPEN_CHAT', payload: payload || {} }, '*')
  }

  window.nexusLinkedInAssist = function nexusLinkedInAssist(payload) {
    return new Promise(function (resolve, reject) {
      var id = 'nexus-li-' + Math.random().toString(36).slice(2)
      var done = false
      function finish(fn, arg) {
        if (done) return
        done = true
        window.clearTimeout(timer)
        window.removeEventListener('message', onResponse)
        fn(arg)
      }
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_LINKEDIN_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        if (ev.data.ok) finish(resolve, ev.data.result)
        else finish(reject, new Error(ev.data.error || 'La extensión no pudo abrir LinkedIn'))
      }
      window.addEventListener('message', onResponse)
      var timer = window.setTimeout(function () {
        finish(reject, new Error('timeout_extension_assist'))
      }, 60000)
      window.postMessage({ type: 'NEXUS_LINKEDIN_REQUEST', id: id, payload: payload }, '*')
    })
  }

  window.nexusLinkedInSetPending = function nexusLinkedInSetPending(payload) {
    return new Promise(function (resolve, reject) {
      var id = 'nexus-li-pending-' + Math.random().toString(36).slice(2)
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_LINKEDIN_PENDING_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        window.removeEventListener('message', onResponse)
        if (ev.data.ok) resolve(ev.data.result || { ok: true })
        else reject(new Error(ev.data.error || 'No se pudo registrar el envío pendiente'))
      }
      window.addEventListener('message', onResponse)
      window.postMessage({ type: 'NEXUS_SET_LINKEDIN_PENDING', id: id, payload: payload }, '*')
    })
  }

  window.nexusLinkedInArmInboundWatch = function nexusLinkedInArmInboundWatch(payload) {
    return new Promise(function (resolve) {
      var id = 'nexus-li-arm-watch-' + Math.random().toString(36).slice(2)
      var settled = false
      function finish(value) {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        window.removeEventListener('message', onResponse)
        resolve(value || { ok: false })
      }
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_LI_ARM_INBOUND_WATCH_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        finish(ev.data.result || { ok: Boolean(ev.data.ok) })
      }
      window.addEventListener('message', onResponse)
      var timer = window.setTimeout(function () {
        finish({ ok: false, error: 'timeout_arm_inbound_watch' })
      }, 8000)
      window.postMessage({ type: 'NEXUS_LI_ARM_INBOUND_WATCH', id: id, payload: payload || {} }, '*')
    })
  }

  /** LI-IN: fuerza lectura de Messaging y devuelve diagnóstico. */
  window.nexusLinkedInPollInboundNow = function nexusLinkedInPollInboundNow(payload) {
    return new Promise(function (resolve) {
      var id = 'nexus-li-poll-now-' + Math.random().toString(36).slice(2)
      var settled = false
      function finish(value) {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        window.removeEventListener('message', onResponse)
        resolve(value || { ok: false })
      }
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_LI_POLL_INBOUND_NOW_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        finish(ev.data.result || { ok: Boolean(ev.data.ok) })
      }
      window.addEventListener('message', onResponse)
      var timer = window.setTimeout(function () {
        finish({ ok: false, reason: 'timeout', error: 'timeout_poll_inbound' })
      }, 20000)
      window.postMessage({ type: 'NEXUS_LI_POLL_INBOUND_NOW', id: id, payload: payload || {} }, '*')
    })
  }

  window.nexusLinkedInProbeConnection = function nexusLinkedInProbeConnection(payload) {
    return new Promise(function (resolve) {
      var id = 'nexus-li-probe-' + Math.random().toString(36).slice(2)
      var settled = false
      function finish(value) {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        window.removeEventListener('message', onResponse)
        resolve(value)
      }
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_PROBE_LINKEDIN_CONNECTION_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        // Siempre resolve con el payload (ok o fail) — no reject, así Nexus muestra el error.
        var result = ev.data.result || {}
        if (ev.data.ok === false && !result.error) {
          result = Object.assign({}, result, {
            ok: false,
            readOk: false,
            error: ev.data.error || 'probe_failed',
          })
        }
        finish(result)
      }
      var timer = window.setTimeout(function () {
        finish({
          ok: false,
          readOk: false,
          error: 'timeout_extension_probe',
          prospectId: payload && payload.prospectId,
          prospectName: payload && payload.prospectName,
        })
      }, 42000)
      window.addEventListener('message', onResponse)
      window.postMessage({ type: 'NEXUS_PROBE_LINKEDIN_CONNECTION', id: id, payload: payload }, '*')
    })
  }

  /** Dispara YA el sondeo de checking pendientes (sin esperar alarm). */
  window.nexusLinkedInProbePendingNow = function nexusLinkedInProbePendingNow() {
    return new Promise(function (resolve, reject) {
      var id = 'nexus-li-probe-all-' + Math.random().toString(36).slice(2)
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        window.removeEventListener('message', onResponse)
        if (ev.data.ok) resolve(ev.data.result || { ok: true })
        else reject(new Error(ev.data.error || 'No se pudo verificar pendientes'))
      }
      window.addEventListener('message', onResponse)
      window.postMessage({ type: 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW', id: id }, '*')
    })
  }

  window.nexusWhatsAppArmOpenChat = function nexusWhatsAppArmOpenChat(payload) {
    window.postMessage({ type: 'NEXUS_ARM_WHATSAPP_CHAT', payload: payload || {} }, '*')
  }

  window.nexusWhatsAppSetPending = function nexusWhatsAppSetPending(payload) {
    return new Promise(function (resolve, reject) {
      var id = 'nexus-wa-pending-' + Math.random().toString(36).slice(2)
      function onResponse(ev) {
        if (
          ev.source !== window ||
          !ev.data ||
          ev.data.type !== 'NEXUS_WHATSAPP_PENDING_RESPONSE' ||
          ev.data.id !== id
        ) {
          return
        }
        window.removeEventListener('message', onResponse)
        if (ev.data.ok) resolve(ev.data.result || { ok: true })
        else reject(new Error(ev.data.error || 'No se pudo registrar WhatsApp pendiente'))
      }
      window.addEventListener('message', onResponse)
      window.postMessage({ type: 'NEXUS_SET_WHATSAPP_PENDING', id: id, payload: payload }, '*')
    })
  }

  window.nexusOpenExtensionsPage = function nexusOpenExtensionsPage() {
    window.postMessage({ type: 'NEXUS_OPEN_EXTENSIONS_PAGE' }, '*')
  }

  /** Pausa / handoff / eliminar: deja de vigilar WA+LI para ese prospecto. */
  window.nexusClearProspectWatch = function nexusClearProspectWatch(prospectId) {
    window.postMessage(
      {
        type: 'NEXUS_CLEAR_PROSPECT_WATCH',
        payload: { prospectId: Number(prospectId) || 0 },
      },
      '*',
    )
  }
})()
