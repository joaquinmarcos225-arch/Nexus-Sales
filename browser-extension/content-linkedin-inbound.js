/**
 * LI-IN (LI-SAFE): observa Messaging SOLO si /messaging está abierto.
 * Solo lee DOM. Sin Voyager. Sin abrir tabs. Sin pegar/enviar.
 * Identidad estricta: hace falta slug claro del interlocutor.
 */
const INBOUND_WATCHER_GEN = '0.18.55-li-in'
const LI_SAFE_INBOUND = true
/** Poll local: más frecuente si hay watch activo. */
const POLL_MS = LI_SAFE_INBOUND ? 20_000 : 4000
const POLL_WATCHING_MS = LI_SAFE_INBOUND ? 8_000 : 2000
const SEEN_KEY = 'nexusLiInboundSeen'
const MAX_SEEN = 400
let mutationTimer = null
let pollInFlight = false
let pollIntervalId = null
let spaHookBound = false

if (window.__NEXUS_LI_INBOUND_GEN__ !== INBOUND_WATCHER_GEN) {
  window.__NEXUS_LI_INBOUND_GEN__ = INBOUND_WATCHER_GEN
  if (pollIntervalId != null) clearInterval(pollIntervalId)
  pollIntervalId = null
  void bootInboundWatcher()
}

/** /messaging O overlay de chats (LinkedIn suele dejar el feed con el chat abierto). */
function isMessagingContext() {
  try {
    const path = String(location.pathname || '').toLowerCase()
    if (path.includes('/messaging')) return true
  } catch {
    /* ignore */
  }
  try {
    return Boolean(
      document.querySelector(
        [
          '.msg-overlay-list-bubble',
          '.msg-overlay-conversation-bubble',
          '.msg-overlay-bubble-header',
          '.msg-overlay-list-bubble__conversations-list',
          '.msg-form',
          '.msg-s-message-list-container',
          '.msg-conversations-container',
        ].join(', '),
      ),
    )
  } catch {
    return false
  }
}

function bootInboundWatcher() {
  if (!window.location.hostname.includes('linkedin.com')) return

  if (window.__NEXUS_LI_INBOUND_MSG_BOUND__ !== INBOUND_WATCHER_GEN) {
    window.__NEXUS_LI_INBOUND_MSG_BOUND__ = INBOUND_WATCHER_GEN
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === 'NEXUS_PING_INBOUND') {
        sendResponse({
          ok: true,
          messaging: isMessagingContext(),
          messagingPath: String(location.pathname || ''),
          liSafe: LI_SAFE_INBOUND,
        })
        return false
      }
      if (message?.type === 'NEXUS_POLL_INBOUND') {
        void pollInbound('alarm')
          .then((r) => sendResponse({ ok: true, ...(r || {}) }))
          .catch(() => sendResponse({ ok: false }))
        return true
      }
      if (message?.type === 'NEXUS_LI_WATCH_ARMED') {
        if (!isMessagingContext()) {
          sendResponse?.({ ok: false, reason: 'no_messaging_context' })
          return false
        }
        void pollInbound('watch-armed')
        sendResponse?.({ ok: true })
        return false
      }
      return false
    })
  }

  // LinkedIn es SPA: al pasar a Messaging / abrir overlay, arrancar watcher.
  if (!spaHookBound) {
    spaHookBound = true
    const onMaybeMessaging = () => {
      if (isMessagingContext()) initInboundWatcher()
    }
    window.addEventListener('popstate', onMaybeMessaging)
    window.addEventListener('hashchange', onMaybeMessaging)
    const origPush = history.pushState
    const origReplace = history.replaceState
    history.pushState = function (...args) {
      const r = origPush.apply(this, args)
      onMaybeMessaging()
      return r
    }
    history.replaceState = function (...args) {
      const r = origReplace.apply(this, args)
      onMaybeMessaging()
      return r
    }
    // Overlay puede montarse sin cambiar URL.
    window.setInterval(onMaybeMessaging, 5000)
  }

  if (LI_SAFE_INBOUND && !isMessagingContext()) return
  initInboundWatcher()
}

function initInboundWatcher() {
  if (!isMessagingContext()) return
  void pollInbound('init')
  if (pollIntervalId != null) clearInterval(pollIntervalId)
  pollIntervalId = setInterval(() => void pollInbound('interval'), POLL_MS)
  pollIntervalId.__nexusMs = POLL_MS

  const root = document.body || document.documentElement
  if (root && window.__NEXUS_LI_INBOUND_OBS_GEN__ !== INBOUND_WATCHER_GEN) {
    window.__NEXUS_LI_INBOUND_OBS_GEN__ = INBOUND_WATCHER_GEN
    const observer = new MutationObserver(() => {
      if (!isMessagingContext()) return
      if (mutationTimer) return
      mutationTimer = window.setTimeout(() => {
        mutationTimer = null
        void pollInbound('mutation')
      }, 1500)
    })
    observer.observe(root, { childList: true, subtree: true })
  }
}

async function syncPollInterval(watching) {
  const want = watching ? POLL_WATCHING_MS : POLL_MS
  if (pollIntervalId != null && pollIntervalId.__nexusMs === want) return
  if (pollIntervalId != null) clearInterval(pollIntervalId)
  pollIntervalId = setInterval(() => void pollInbound('interval'), want)
  pollIntervalId.__nexusMs = want
}

/**
 * Solo DOM. Sin Voyager.
 */
async function pollInbound(_source) {
  if (pollInFlight) return { skipped: true, reason: 'in_flight' }
  if (LI_SAFE_INBOUND && !isMessagingContext()) {
    return { skipped: true, reason: 'not_messaging_context' }
  }
  pollInFlight = true
  try {
    await injectUtils()
    const api = window.__NEXUS_LI_INBOUND__
    if (!api) return { ok: false, reason: 'no_utils' }

    const stored = await chrome.storage.local.get([
      SEEN_KEY,
      'nexusLiWatch',
      'nexusLiLastProspectId',
      'nexusLiLastProfileSlug',
      'nexusLiLastOutboundText',
      'nexusLiWatchUntil',
      'nexusLiPendingSend',
    ])
    const seen = stored?.[SEEN_KEY] || {}
    const watch = stored?.nexusLiWatch || {}
    const watchSlugRaw = String(
      watch?.profileSlug || stored?.nexusLiLastProfileSlug || '',
    ).trim()
    const watchSlug = api.normalizeLiSlug?.(watchSlugRaw) || watchSlugRaw.toLowerCase()
    const watchProspectId = Number(
      watch?.prospectId || stored?.nexusLiLastProspectId || 0,
    )
    const ours = String(
      stored?.nexusLiLastOutboundText ||
        stored?.nexusLiPendingSend?.messagePrefix ||
        '',
    )
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase()
    const watchUntil = Number(stored?.nexusLiWatchUntil || 0)
    const watching =
      (Boolean(watchSlug) || Boolean(watchProspectId)) &&
      (!watchUntil || Date.now() < watchUntil)
    void syncPollInterval(watching)

    function sameWatchSlug(slug) {
      if (api.slugsMatch) return api.slugsMatch(slug, watchSlugRaw || watchSlug)
      return String(slug || '').toLowerCase() === watchSlug
    }

    /** @type {{ slug: string, message: string, source: string, prospectId?: number }[]} */
    const candidates = []
    const seenKey = new Set()

    function isEcho(text) {
      const t = String(text || '')
        .replace(/\s+/g, ' ')
        .trim()
        .toLowerCase()
      if (!ours || ours.length < 12 || t.length < 12) return false
      return (
        t.slice(0, 80) === ours.slice(0, 80) ||
        t.includes(ours.slice(0, 60)) ||
        ours.includes(t.slice(0, 60))
      )
    }

    function pushCandidate(slug, message, source) {
      const s = String(slug || '').trim().toLowerCase()
      const m = String(message || '').trim()
      if (!m) return
      // LI-SAFE: sin slug real no atribuimos (evita chat equivocado).
      if (!s || s === 'unknown' || s === 'watched') return
      if (api.isNoiseMessage?.(m)) return
      if (isEcho(m)) return
      const key = `${s}::${m.slice(0, 200)}`
      if (seenKey.has(key)) return
      seenKey.add(key)
      let prospectId
      if (watchProspectId && sameWatchSlug(s)) {
        prospectId = watchProspectId
      }
      candidates.push({
        slug: s,
        message: m,
        source,
        prospectId,
      })
    }

    // Thread abierto: partner + inbound (por clase, por nombre de grupo, o watch).
    let activeSlug = String(api.extractPartnerSlug?.() || '')
      .trim()
      .toLowerCase()
    const threadOpen = Boolean(
      document.querySelector(
        '.msg-s-message-list, .msg-thread, .msg-overlay-conversation-bubble__content-wrapper, .msg-s-message-list-container',
      ),
    )
    const threadName = api.extractOpenThreadParticipantName?.() || ''
    const watchName = String(watch?.prospectName || '')
    if (!activeSlug && watching && watchSlug && threadOpen) {
      if (!watchName || !threadName || api.namesLooselyMatch?.(threadName, watchName)) {
        activeSlug = watchSlug
      }
    }
    const partnerForName = threadName || watchName
    let activeText = api.extractLatestInboundIfTheySpokeLast?.() || null
    if (!activeText && partnerForName) {
      activeText = api.extractLatestInboundByPartnerName?.(partnerForName) || null
    }
    if (!activeText && watching && threadOpen) {
      activeText =
        api.extractLatestThreadMessageForWatch?.() ||
        api.extractLatestInboundMessage?.() ||
        null
    }
    if (activeText && activeSlug) {
      if (!isEcho(activeText) && !api.looksLikeOutboundSnippet?.(activeText)) {
        pushCandidate(activeSlug, activeText, 'thread')
      }
    }
    // Watch sin slug en DOM pero con texto inbound: atribuimos al watch.
    if (
      !candidates.length &&
      watching &&
      watchSlug &&
      threadOpen &&
      activeText &&
      !isEcho(activeText) &&
      !api.looksLikeOutboundSnippet?.(activeText)
    ) {
      pushCandidate(watchSlug, activeText, 'thread-watch-name')
    }

    // Lista: unread, o watch por slug/nombre. Con watch, aceptar también leídos.
    for (const preview of api.scanConversationPreviews?.() || []) {
      if (!preview.text) continue
      if (api.looksLikeOutboundSnippet?.(preview.text)) continue
      if (isEcho(preview.text)) continue
      let slug = preview.slug ? String(preview.slug).toLowerCase() : ''
      if (!slug && watching && watchSlug && preview.participantName) {
        const watchName = String(watch?.prospectName || '')
        if (watchName && api.namesLooselyMatch?.(preview.participantName, watchName)) {
          slug = watchSlug
        }
      }
      if (!slug) continue
      if (LI_SAFE_INBOUND && !preview.unread) {
        if (!(watching && watchSlug && sameWatchSlug(slug))) continue
      }
      pushCandidate(slug, preview.text, preview.unread ? 'unread' : 'preview-watch')
    }

    // Watch por nombre si la lista no trae /in/.
    if (watching && watchSlug && candidates.length === 0) {
      const byName = api.findConversationPreviewByName?.(watch?.prospectName || '')
      if (
        byName?.text &&
        !api.looksLikeOutboundSnippet?.(byName.text) &&
        !isEcho(byName.text)
      ) {
        pushCandidate(watchSlug, byName.text, 'name-watch')
      }
    }

    // Sin Voyager / fetchConversationPreviewsViaApi en LI-SAFE.

    for (const item of candidates) {
      const fingerprint = api.fingerprintMessage(item.slug, item.message)
      if (seen[fingerprint]) continue
      await reportInbound(item.slug, item.message, fingerprint, seen, item.prospectId)
    }
    const domDiag = api.collectInboundDomDiag?.() || null
    return {
      ok: true,
      candidates: candidates.length,
      watching,
      threadOpen,
      watchSlug: watchSlug || null,
      watchProspectId: watchProspectId || null,
      domDiag,
    }
  } finally {
    pollInFlight = false
  }
}

async function reportInbound(slug, text, fingerprint, seen, prospectIdHint) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      {
        type: 'NEXUS_LINKEDIN_INBOUND_DETECTED',
        profileSlug: slug,
        message: text,
        linkedinMessageId: fingerprint,
        prospectId: prospectIdHint || undefined,
        detectedAt: Date.now(),
      },
      (response) => {
        if (chrome.runtime.lastError) {
          try {
            chrome.storage.local.set({
              nexusLiLastInboundError: {
                at: Date.now(),
                error: chrome.runtime.lastError.message,
                slug,
              },
            })
          } catch {
            /* ignore */
          }
          resolve({ ok: false })
          return
        }
        if (response?.ok && (response?.inserted || response?.duplicate || response?.echo_ignored)) {
          seen[fingerprint] = Date.now()
          pruneSeen(seen)
          chrome.storage.local.set({ [SEEN_KEY]: seen })
        }
        if (response?.ok && response?.inserted) {
          showToast('Nexus: respuesta LinkedIn detectada → borrador en cola')
        } else if (!response?.ok) {
          try {
            chrome.storage.local.set({
              nexusLiLastInboundError: {
                at: Date.now(),
                error: response?.error || 'report_failed',
                slug,
              },
            })
          } catch {
            /* ignore */
          }
        }
        resolve(response || { ok: false })
      },
    )
  })
}

function pruneSeen(seen) {
  const entries = Object.entries(seen || {})
  if (entries.length <= MAX_SEEN) return
  entries.sort((a, b) => Number(a[1]) - Number(b[1]))
  const drop = entries.length - MAX_SEEN
  for (let i = 0; i < drop; i += 1) {
    delete seen[entries[i][0]]
  }
}

async function injectUtils() {
  if (window.__NEXUS_LI_INBOUND__) return
  // utils vienen por content_scripts; esperar un tic.
  await new Promise((r) => setTimeout(r, 50))
}

function showToast(text) {
  try {
    let el = document.getElementById('nexus-li-inbound-toast')
    if (!el) {
      el = document.createElement('div')
      el.id = 'nexus-li-inbound-toast'
      el.style.cssText =
        'position:fixed;z-index:2147483647;bottom:16px;right:16px;max-width:280px;' +
        'padding:10px 12px;border-radius:10px;background:#0A66C2;color:#fff;' +
        'font:12px/1.35 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25)'
      document.documentElement.appendChild(el)
    }
    el.textContent = String(text || '')
    el.style.display = 'block'
    window.setTimeout(() => {
      if (el) el.style.display = 'none'
    }, 4500)
  } catch {
    /* ignore */
  }
}
