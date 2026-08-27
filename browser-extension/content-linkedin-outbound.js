/**
 * Detecta envío manual en LinkedIn (Enter / Enviar / composer vacío + mensaje en hilo).
 * NUNCA auto-envía. Sin evidencia de envío, no toca la cola de Nexus.
 */
const POLL_MS = 900
const PENDING_KEY = 'nexusLiPendingSend'
const SENT_FP_KEY = 'nexusLiOutboundSent'

let lastGestureAt = 0
let sawFilledComposer = false

void initOutboundWatcher()

function initOutboundWatcher() {
  if (!window.location.hostname.includes('linkedin.com')) return
  void pollOutbound(false)
  setInterval(() => void pollOutbound(false), POLL_MS)
  document.addEventListener('click', onDocClick, true)
  document.addEventListener('keydown', onDocKey, true)
  document.addEventListener('keyup', onDocKey, true)
  window.addEventListener('keydown', onDocKey, true)
  try {
    const mo = new MutationObserver(() => {
      if (Date.now() - lastGestureAt < 8000) void pollOutbound(true)
    })
    mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true })
  } catch {
    /* ignore */
  }
}

function onDocClick(event) {
  const target = event.target
  if (!target?.closest) return
  const inMsg = target.closest('form.msg-form, .msg-form, .msg-overlay, .msg-overlay-conversation-bubble')
  if (!inMsg) return

  const btn = target.closest(
    [
      'button.msg-form__send-button',
      'button[type="submit"].msg-form__send-button',
      'button.msg-form__send-btn',
      'button[type="submit"]',
      'button[data-control-name*="send" i]',
      'button[aria-label*="Enviar" i]',
      'button[aria-label*="Send" i]',
      '.msg-form__send-button',
    ].join(', '),
  )
  if (btn) {
    markGesture()
    return
  }

  // Texto del botón (LinkedIn a veces no usa las clases de arriba).
  const clickedBtn = target.closest('button')
  if (clickedBtn) {
    const t = (clickedBtn.innerText || clickedBtn.getAttribute('aria-label') || '')
      .trim()
      .toLowerCase()
    if (t === 'enviar' || t === 'send' || t.startsWith('enviar') || t.startsWith('send')) {
      markGesture()
    }
  }
}

function onDocKey(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  const target = event.target
  if (!isComposerTarget(target)) return
  markGesture()
}

function isComposerTarget(target) {
  if (!target?.closest) return false
  return Boolean(
    target.closest(
      [
        '.msg-form__contenteditable',
        '.msg-form__msg-content-container',
        '.msg-form',
        'div.msg-form__contenteditable',
        '[role="textbox"][contenteditable="true"]',
        '.msg-overlay [contenteditable="true"]',
      ].join(', '),
    ),
  )
}

function markGesture() {
  lastGestureAt = Date.now()
  scheduleSendChecks()
}

function scheduleSendChecks() {
  ;[250, 700, 1400, 2500, 4000, 7000, 10000].forEach((ms) => {
    window.setTimeout(() => void pollOutbound(true), ms)
  })
}

async function pollOutbound(fromSendGesture = false) {
  const stored = await chrome.storage.local.get(PENDING_KEY)
  const pending = stored?.[PENDING_KEY]
  if (!pending?.prospectId || !pending?.messagePrefix) return

  const ageMs = Date.now() - Number(pending.since || 0)
  if (ageMs > 45 * 60 * 1000) {
    await chrome.storage.local.remove(PENDING_KEY)
    sawFilledComposer = false
    return
  }

  const path = window.location.pathname.toLowerCase()
  const onMessaging =
    path.includes('/messaging') ||
    path.includes('/in/') ||
    Boolean(document.querySelector('.msg-form, .msg-overlay, .msg-conversations-container'))
  if (!onMessaging) return

  await injectOutboundUtils()
  const api = window.__NEXUS_LI_OUTBOUND__
  if (!api) return

  // Slug del partner: si no se puede leer, no bloquear (tenemos prospectId).
  const seenSlug = decodeSlug(api.extractPartnerSlug())
  const pendingSlug = decodeSlug(pending.profileSlug)
  if (seenSlug && pendingSlug && isLikelyProfileSlug(seenSlug) && !slugsMatch(seenSlug, pendingSlug)) {
    return
  }

  const composerHas = api.composerHasContent()
  const composerMatches = api.composerMatchesPrefix?.(pending.messagePrefix)
  if (composerHas && (composerMatches || !api.composerMatchesPrefix)) {
    sawFilledComposer = true
  }

  const recentGesture = fromSendGesture || Date.now() - lastGestureAt < 12000
  const sentInThread = api.detectOutboundSent(pending.messagePrefix)
  const composerEmpty = !composerHas
  const lastOut = api.lastOutboundBody()
  const lastMatches = lastOut.length >= 6 && textsRoughMatch(pending.messagePrefix, lastOut)

  // Evidencia fuerte: el borrador aparece como mensaje saliente en el hilo.
  // Evidencia tras gesto: composer vacío + último saliente coincide (o se vació tras haber pegado).
  let confirmed = false
  if (sentInThread && (recentGesture || (sawFilledComposer && composerEmpty))) {
    confirmed = true
  } else if (recentGesture && composerEmpty && (lastMatches || (sawFilledComposer && lastOut.length >= 6))) {
    confirmed = true
  } else if (recentGesture && composerEmpty && sawFilledComposer) {
    // Tras Enter/Enviar el composer se vació: suficiente si acabamos de pegar desde Nexus.
    const pastedRecently =
      pending.pastedAt && Date.now() - Number(pending.pastedAt) < 30 * 60 * 1000
    if (pastedRecently || ageMs < 30 * 60 * 1000) confirmed = true
  }

  if (!confirmed) return

  await reportSent(pending, (isLikelyProfileSlug(seenSlug) && seenSlug) || pending.profileSlug, {
    isReply: Boolean(pending.isReply),
  })
  sawFilledComposer = false
}

function isLikelyProfileSlug(slug) {
  const s = decodeSlug(slug)
  if (!s) return false
  // Thread UUIDs de LinkedIn: "2-xxxx..." o strings muy largos con pocos guiones de nombre.
  if (/^\d+-/.test(s)) return false
  if (s.length > 90) return false
  if (/^[0-9a-f-]{30,}$/i.test(s)) return false
  return true
}

function decodeSlug(raw) {
  try {
    return decodeURIComponent(String(raw || '').trim()).toLowerCase()
  } catch {
    return String(raw || '').trim().toLowerCase()
  }
}

function slugsMatch(a, b) {
  const left = decodeSlug(a)
  const right = decodeSlug(b)
  if (!left || !right) return true
  if (left === right) return true
  const strip = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  if (strip(left) === strip(right)) return true
  const lt = left.split('-').pop()
  const rt = right.split('-').pop()
  return Boolean(lt && rt && lt.length >= 6 && lt === rt)
}

function textsRoughMatch(expected, actual) {
  const a = String(expected || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .slice(0, 100)
  const b = String(actual || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .slice(0, 100)
  if (!a || !b) return false
  const head = a.slice(0, Math.min(40, a.length))
  return b.includes(head) || a.includes(b.slice(0, Math.min(40, b.length)))
}

async function reportSent(pending, slug, meta = {}) {
  const fp = `sent:${pending.prospectId}:${pending.messageHash || pending.messagePrefix.slice(0, 40)}`
  const stored = await chrome.storage.local.get(SENT_FP_KEY)
  const seen = stored?.[SENT_FP_KEY] || {}
  if (seen[fp]) return

  // Reservar fingerprint para no spamear la API mientras responde.
  seen[fp] = Date.now()
  await chrome.storage.local.set({ [SENT_FP_KEY]: seen })

  chrome.runtime.sendMessage(
    {
      type: 'NEXUS_LINKEDIN_OUTBOUND_SENT',
      profileSlug: slug,
      prospectId: pending.prospectId,
      autoDetected: true,
    },
    (response) => {
      if (chrome.runtime.lastError) {
        delete seen[fp]
        chrome.storage.local.set({ [SENT_FP_KEY]: seen })
        console.warn('[Nexus LI] mark-sent runtime error', chrome.runtime.lastError.message)
        return
      }
      if (!response?.ok) {
        delete seen[fp]
        chrome.storage.local.set({ [SENT_FP_KEY]: seen })
        console.warn('[Nexus LI] mark-sent failed', response)
        showToast(`Nexus: no pude marcar el envío (${response?.error || 'error'})`, true)
        return
      }
      chrome.storage.local.remove(PENDING_KEY)
      showToast(
        meta.isReply
          ? 'Nexus: réplica marcada como enviada — salió de la cola'
          : 'Nexus: mensaje marcado como enviado — salió de la cola',
      )
    },
  )
}

async function injectOutboundUtils() {
  if (window.__NEXUS_LI_OUTBOUND__) return
  try {
    await chrome.runtime.sendMessage({ type: 'NEXUS_INJECT_OUTBOUND_UTILS' })
  } catch (err) {
    console.warn('[Nexus LI] inject outbound utils failed', err)
  }
}

function showToast(msg, isError = false) {
  const id = 'nexus-li-outbound-toast'
  let el = document.getElementById(id)
  if (!el) {
    el = document.createElement('div')
    el.id = id
    el.style.cssText =
      'position:fixed;bottom:20px;right:20px;z-index:99999;color:#fff;' +
      'padding:12px 16px;border-radius:10px;font:13px/1.4 system-ui,sans-serif;' +
      'box-shadow:0 4px 20px rgba(0,0,0,.25);max-width:360px'
    document.body.appendChild(el)
  }
  el.style.background = isError ? '#b91c1c' : '#059669'
  el.textContent = msg
  setTimeout(() => el?.remove(), isError ? 10000 : 7000)
}
