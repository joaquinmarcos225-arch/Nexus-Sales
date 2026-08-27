/**
 * WhatsApp Web assist: paste + detectar envío manual (nunca auto-click Enviar).
 * Inbound: Store interno de WA (prioridad) + lista + open solo en background.
 */
const WA_PENDING_KEY = 'nexusWaPendingSend'
const WA_SENT_FP_KEY = 'nexusWaOutboundSent'
const WA_WATCH_LIST_KEY = 'nexusWaWatchList'
const WA_POLL_MS = 1000
const WA_WATCH_MAX = 40
const WA_WATCH_TTL_MS = 48 * 60 * 60 * 1000
const WA_WATCH_CURSOR_KEY = 'nexusWaWatchCursor'
const WA_LIST_SCAN_MAX = 40
const WA_OPEN_BATCH_DEFAULT = 8
const WA_REPORT_BATCH_DEFAULT = 16

let lastGestureAt = 0
let sawFilledComposer = false
/** Evita doble pegado concurrente (background + retry) que concatena basura en Lexical. */
let pasteInFlight = null
let pasteLockUntil = 0

if (!globalThis.__NEXUS_WA_ASSIST_INIT__) {
  globalThis.__NEXUS_WA_ASSIST_INIT__ = true
  void initWhatsAppAssist()
} else {
  // Reinyección: no tocar el composer (evita pegar otra vez).
}

function initWhatsAppAssist() {
  if (!/web\.whatsapp\.com/i.test(window.location.hostname)) return

  injectWhatsAppNotificationHook()
  void pollWhatsAppOutbound(false)
  setInterval(() => void pollWhatsAppOutbound(false), WA_POLL_MS)

  // Una sola pestaña WA del usuario: vigilancia pasiva (lista + notificaciones). Sin 2ª pestaña.
  void pollWhatsAppInbound('init')
  setInterval(() => void pollWhatsAppInbound('interval'), 3000)
  watchInboundDomMutations()
  let lastWaTitle = document.title || ''
  let lastWaUnread = hasWhatsAppUnreadSignal()
  setInterval(() => {
    const title = document.title || ''
    const unread = hasWhatsAppUnreadSignal()
    const titleHit = title !== lastWaTitle && /\(\d+\)/.test(title)
    const unreadHit = unread && !lastWaUnread
    lastWaTitle = title
    lastWaUnread = unread
    if (titleHit || unreadHit) {
      void pollWhatsAppInbound(titleHit ? 'title' : 'unread-badge')
    }
  }, 1500)

  document.addEventListener('click', onWaClick, true)
  document.addEventListener('keydown', onWaKey, true)
  window.addEventListener('keydown', onWaKey, true)
  window.addEventListener('message', onWaPageMessage)

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'NEXUS_WA_PASTE') {
      void pasteWhatsAppMessageOnce(message.message)
        .then((ok) => sendResponse({ ok: Boolean(ok), pasted: Boolean(ok) }))
        .catch(() => sendResponse({ ok: false, pasted: false }))
      return true
    }
    if (message?.type === 'NEXUS_WA_CHECK_SESSION') {
      sendResponse({ ok: true, loggedIn: isWhatsAppLoggedIn() })
      return false
    }
    if (message?.type === 'NEXUS_WA_COMPOSER_READY') {
      sendResponse({
        ok: true,
        ready: Boolean(isWhatsAppLoggedIn() && findComposer()),
        loggedIn: isWhatsAppLoggedIn(),
      })
      return false
    }
    if (message?.type === 'NEXUS_POLL_WA_INBOUND') {
      void pollWhatsAppInbound(message?.reason || 'alarm')
        .then((r) => sendResponse({ ok: true, ...(r || {}) }))
        .catch(() => sendResponse({ ok: false }))
      return true
    }
    return false
  })

  // Sin fallbacks de pegado: una sola inserción la dispara el background.
}

/** Pide al background inyectar hook de Notification (CSP de WA bloquea <script> inline). */
function injectWhatsAppNotificationHook() {
  try {
    chrome.runtime.sendMessage({ type: 'NEXUS_INJECT_WA_NOTIFY_HOOK' }, () => {
      void chrome.runtime.lastError
    })
  } catch {
    /* ignore */
  }
}

function showNexusWaWatchBadge(_text) {
  // Silencioso en producto: los carteles de vigilancia eran solo para debug.
  const el = document.getElementById('nexus-wa-watch-badge')
  if (el) el.remove()
}

function scrapeAllVisibleMessageTexts() {
  const main = document.querySelector('#main')
  if (!main) return []
  const nodes = main.querySelectorAll(
    'span.selectable-text.copyable-text, span.selectable-text, [data-testid="conversation-text"]',
  )
  const texts = []
  for (const n of nodes) {
    const t = String(n.innerText || n.textContent || '')
      .replace(/\s+/g, ' ')
      .trim()
    if (t.length < 2) continue
    if (/^\d{1,2}:\d{2}$/.test(t)) continue
    if (/^(hoy|ayer|today|yesterday)$/i.test(t)) continue
    texts.push(t.slice(0, 500))
  }
  const uniq = []
  for (const t of texts) {
    if (uniq[uniq.length - 1] === t) continue
    uniq.push(t)
  }
  return uniq
}

function textsAfterOursLocal(texts, ours) {
  const list = Array.isArray(texts) ? texts : []
  const o = String(ours || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  if (!o || o.length < 12) return list.slice(-2)
  const head = o.slice(0, Math.min(50, o.length))
  let lastOurs = -1
  for (let i = 0; i < list.length; i += 1) {
    const t = String(list[i] || '')
      .toLowerCase()
      .replace(/\s+/g, ' ')
    if (t.includes(head) || head.includes(t.slice(0, 40))) lastOurs = i
  }
  if (lastOurs < 0) return list.slice(-3)
  return list.slice(lastOurs + 1)
}

function onWaPageMessage(ev) {
  if (ev.source !== window || !ev.data || ev.data.type !== 'NEXUS_WA_NATIVE_NOTIFICATION') return
  const title = String(ev.data.title || '').trim()
  const body = String(ev.data.body || '').trim()
  if (!body && !title) return
  // Disparar captura: abrir no leídos / chat reciente y registrar.
  void pollWhatsAppInbound('notification')
  // Si el body parece mensaje real y tenemos prospecto reciente, reportar también por hint.
  if (body.length >= 1) {
    void reportInboundFromNotification(title, body)
  }
}

async function reportInboundFromNotification(title, body) {
  // Notificación nativa: atribuir solo si el título matchea watch list (nombre/tel).
  if (await isEchoOfOurOutbound(body)) return
  const watchList = await loadWaWatchList()
  const hints = await getWaChatHints()
  const titleDigits = String(title || '').replace(/\D/g, '')
  let matched = matchWatchTarget(watchList, { title, phone: titleDigits })
  if (!matched) {
    const knownName = await getWatchedProspectName()
    if (knownName && namesMatchStrong(knownName, title)) {
      matched = {
        prospectId: hints.prospectId,
        phone: hints.phone,
        name: knownName,
      }
    } else if (hints.phone && titleDigits.length >= 8 && waDigitsMatch(titleDigits, hints.phone)) {
      matched = { prospectId: hints.prospectId, phone: hints.phone, name: knownName }
    }
  }
  if (!matched) return
  await reportWhatsAppInboundIfNew(
    matched.phone || hints.phone || (titleDigits.length >= 8 ? titleDigits : '0'),
    cleanWaPreviewText(body).slice(0, 500) || body.slice(0, 500),
    matched.prospectId || hints.prospectId || undefined,
  )
}

function hasWhatsAppUnreadSignal() {
  if (/\(\d+\)/.test(document.title || '')) return true
  if (
    document.querySelector(
      '#pane-side [data-testid="icon-unread-count"], #pane-side span[aria-label*="no leído" i], #pane-side span[aria-label*="unread" i]',
    )
  ) {
    return true
  }
  return false
}

function isWhatsAppLoggedIn() {
  // QR / landing: no hay composer ni lista de chats.
  if (document.querySelector('canvas[aria-label*="QR" i], div[data-ref] canvas')) {
    // Puede ser QR — si también hay composer, estamos adentro.
    if (!findComposer()) return false
  }
  return Boolean(
    document.querySelector('#pane-side') ||
      document.querySelector('[data-testid="chat-list"]') ||
      findComposer(),
  )
}

function findComposer() {
  return (
    document.querySelector('footer div[contenteditable="true"][data-tab]') ||
    document.querySelector('footer [contenteditable="true"][role="textbox"]') ||
    document.querySelector('div[contenteditable="true"][data-tab="10"]') ||
    document.querySelector('footer [contenteditable="true"]')
  )
}

function composerText() {
  const el = findComposer()
  return (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim()
}

function onWaClick(event) {
  const t = event.target
  if (!t?.closest) return
  const sendBtn = t.closest(
    'button[aria-label*="Enviar" i], button[aria-label*="Send" i], span[data-icon="send"], [data-testid="send"], button[data-tab]',
  )
  if (!sendBtn) return
  // Solo si parece el botón send
  const icon = sendBtn.querySelector?.('[data-icon="send"]') || sendBtn.closest('[data-icon="send"]')
  const label = (sendBtn.getAttribute('aria-label') || '').toLowerCase()
  if (!icon && !label.includes('enviar') && !label.includes('send') && sendBtn.getAttribute('data-icon') !== 'send') {
    return
  }
  markGesture()
}

function onWaKey(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  const t = event.target
  if (!t?.closest) return
  if (!t.closest('footer') && t.getAttribute('contenteditable') !== 'true') return
  markGesture()
}

function markGesture() {
  lastGestureAt = Date.now()
  ;[300, 800, 1600, 3000, 5500, 9000].forEach((ms) => {
    window.setTimeout(() => void pollWhatsAppOutbound(true), ms)
  })
}

async function markPendingPasteDone() {
  try {
    const stored = await chrome.storage.local.get(WA_PENDING_KEY)
    const pending = stored?.[WA_PENDING_KEY]
    if (!pending) return
    pending.pasteDone = true
    pending.pasteClaimed = true
    pending.pasteInserted = true
    await chrome.storage.local.set({ [WA_PENDING_KEY]: pending })
  } catch {
    /* ignore */
  }
}

/**
 * Punto medio:
 * 1) Esperar el composer
 * 2) Claim (bloquea cualquier 2º pegado)
 * 3) UNA escritura: ClipboardEvent (Lexical). Si quedó vacío, insertText.
 * 4) Nunca reintentar una vez pasteInserted=true
 */
async function pasteWhatsAppMessageOnce(text) {
  const message = String(text || '').trim()
  if (!message) return false

  if (pasteInFlight) {
    try {
      return await pasteInFlight
    } catch {
      return false
    }
  }

  pasteInFlight = (async () => {
    const stored0 = await chrome.storage.local.get(WA_PENDING_KEY)
    const pending0 = stored0?.[WA_PENDING_KEY] || {}
    // Nuevo flujo: ?text= nativo / MAIN world. El content script no escribe.
    if (pending0.skipAutoPaste) {
      return composerHasOurDraft(message)
    }
    const since = Number(pending0.since || 0)
    const shotKey = since + ':' + normalizeComposerCompare(message).slice(0, 100)

    if (
      globalThis.__NEXUS_WA_CLAIMED__ === shotKey ||
      pending0.pasteInserted ||
      pending0.pasteDone
    ) {
      return composerHasOurDraft(message)
    }

    // 1) Esperar composer ANTES del claim (si claimábamos antes, se gastaba el tiro en vacío).
    let box = null
    for (let i = 0; i < 28; i += 1) {
      if (isWhatsAppLoggedIn()) {
        box = findComposer()
        if (box) break
      }
      await sleep(250)
    }
    if (!box) return false

    // Releer pending: otro flujo pudo claimar mientras esperábamos.
    const stored1 = await chrome.storage.local.get(WA_PENDING_KEY)
    const pending1 = stored1?.[WA_PENDING_KEY] || {}
    if (
      globalThis.__NEXUS_WA_CLAIMED__ === shotKey ||
      pending1.pasteInserted ||
      pending1.pasteDone
    ) {
      return composerHasOurDraft(message)
    }

    if (composerHasOurDraft(message) && !composerLooksDuplicated(message)) {
      globalThis.__NEXUS_WA_CLAIMED__ = shotKey
      await markPendingPasteDone()
      sawFilledComposer = true
      return true
    }

    // 2) Claim justo antes de escribir.
    globalThis.__NEXUS_WA_CLAIMED__ = shotKey
    try {
      pending1.pasteInserted = true
      pending1.pasteClaimed = true
      await chrome.storage.local.set({ [WA_PENDING_KEY]: pending1 })
    } catch {
      /* ignore */
    }

    // 3) Una sola escritura al DOM.
    const ok = await writeComposerOnce(box, message)
    sawFilledComposer = Boolean(ok)
    if (ok) await markPendingPasteDone()
    return ok
  })()

  try {
    return await pasteInFlight
  } finally {
    pasteInFlight = null
  }
}

async function pasteWhatsAppMessage(text) {
  return pasteWhatsAppMessageOnce(text)
}

function normalizeComposerCompare(s) {
  return String(s || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

function composerIsExactDraft(text) {
  return normalizeComposerCompare(composerText()) === normalizeComposerCompare(text)
}

function composerHasOurDraft(text) {
  const want = normalizeComposerCompare(text)
  const cur = normalizeComposerCompare(composerText())
  if (!want || !cur) return false
  if (cur === want) return true
  if (want.length >= 12 && cur.includes(want)) return true
  if (want.length >= 30 && cur.includes(want.slice(0, 30))) return true
  return false
}

function composerLooksDuplicated(text) {
  const want = normalizeComposerCompare(text)
  const cur = normalizeComposerCompare(composerText())
  if (!want || !cur || want.length < 12) return false
  let count = 0
  let idx = 0
  while (idx <= cur.length) {
    const found = cur.indexOf(want, idx)
    if (found < 0) break
    count += 1
    idx = found + Math.max(6, Math.floor(want.length * 0.4))
    if (count >= 2) return true
  }
  return cur.includes(want) && cur.length >= Math.floor(want.length * 1.45)
}

function composerAlreadyHasMessage(text) {
  return composerHasOurDraft(text)
}

function selectComposerContents(el) {
  try {
    el.focus()
    const sel = window.getSelection()
    const range = document.createRange()
    range.selectNodeContents(el)
    sel.removeAllRanges()
    sel.addRange(range)
    document.execCommand('selectAll', false, undefined)
  } catch {
    try {
      el.focus()
    } catch {
      /* ignore */
    }
  }
}

/**
 * Una escritura: clear → ClipboardEvent (Lexical).
 * Solo si quedó VACÍO: insertText. Nunca los dos si el primero ya puso texto.
 */
async function writeComposerOnce(el, text) {
  try {
    el.focus()
    selectComposerContents(el)
    try {
      document.execCommand('delete', false, undefined)
    } catch {
      /* ignore */
    }
    await sleep(50)
    selectComposerContents(el)

    try {
      const dt = new DataTransfer()
      dt.setData('text/plain', text)
      el.dispatchEvent(
        new ClipboardEvent('paste', {
          clipboardData: dt,
          bubbles: true,
          cancelable: true,
        }),
      )
    } catch {
      /* ignore */
    }
    await sleep(80)

    if (composerHasOurDraft(text)) return true

    // Fallback solo con composer vacío (si hay basura, no appendear encima).
    if (!composerText()) {
      selectComposerContents(el)
      try {
        document.execCommand('insertText', false, text)
      } catch {
        /* ignore */
      }
      await sleep(50)
    }

    return composerHasOurDraft(text)
  } catch {
    return false
  }
}

async function pollWhatsAppOutbound(fromGesture = false) {
  const stored = await chrome.storage.local.get(WA_PENDING_KEY)
  const pending = stored?.[WA_PENDING_KEY]
  if (!pending?.prospectId || !pending?.messagePrefix) return

  const ageMs = Date.now() - Number(pending.since || 0)
  if (ageMs > 45 * 60 * 1000) {
    await chrome.storage.local.remove(WA_PENDING_KEY)
    sawFilledComposer = false
    return
  }

  if (!isWhatsAppLoggedIn()) return

  const has = composerText().length >= 2
  if (has) sawFilledComposer = true

  const recentGesture = fromGesture || Date.now() - lastGestureAt < 12000
  if (!recentGesture) return

  const empty = !has
  const sentInChat = detectOutboundInChat(pending.messagePrefix)

  let confirmed = false
  if (sentInChat && (recentGesture || (sawFilledComposer && empty))) confirmed = true
  else if (recentGesture && empty && sawFilledComposer) confirmed = true

  if (!confirmed) return
  await reportWaSent(pending)
  sawFilledComposer = false
}

function detectOutboundInChat(expectedPrefix) {
  const expected = String(expectedPrefix || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .slice(0, 80)
  if (expected.length < 10) return false
  const head = expected.slice(0, Math.min(40, expected.length))
  const bubbles = document.querySelectorAll(
    '[data-testid="msg-container"], div.message-out, div[data-id]',
  )
  for (let i = bubbles.length - 1; i >= Math.max(0, bubbles.length - 20); i -= 1) {
    const el = bubbles[i]
    const isOut =
      el.classList?.contains('message-out') ||
      el.querySelector?.('[data-testid="msg-meta"]') ||
      true
    if (!isOut) continue
    const body = (el.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase()
    if (body.includes(head)) return true
  }
  return false
}

async function reportWaSent(pending) {
  const fp = `wa-sent:${pending.prospectId}:${pending.messageHash || pending.messagePrefix.slice(0, 40)}`
  const stored = await chrome.storage.local.get(WA_SENT_FP_KEY)
  const seen = stored?.[WA_SENT_FP_KEY] || {}
  if (seen[fp]) return
  seen[fp] = Date.now()
  const outboundText = String(pending.messagePrefix || pending.message || '')
    .replace(/\s+/g, ' ')
    .trim()
  const prospectName = String(pending.prospectName || '').trim()
  const phoneDigits = String(pending.phoneDigits || '').replace(/\D/g, '')
  await upsertWaWatchTarget({
    prospectId: pending.prospectId,
    phoneDigits,
    prospectName,
    outboundText,
  })
  await chrome.storage.local.set({
    [WA_SENT_FP_KEY]: seen,
    nexusWaLastOutboundText: outboundText.slice(0, 240),
    nexusWaLastOutboundAt: Date.now(),
    // Evita que el preview/burbuja de NUESTRO envío se registre como inbound.
    nexusWaSuppressInboundUntil: Date.now() + 90 * 1000,
    nexusWaLastChatPhone: phoneDigits,
    nexusWaLastProspectId: Number(pending.prospectId) || null,
    ...(prospectName ? { nexusWaLastProspectName: prospectName } : {}),
  })

  chrome.runtime.sendMessage(
    {
      type: 'NEXUS_WHATSAPP_OUTBOUND_SENT',
      prospectId: pending.prospectId,
      phoneDigits: pending.phoneDigits,
    },
    (response) => {
      if (chrome.runtime.lastError || !response?.ok) {
        delete seen[fp]
        chrome.storage.local.set({ [WA_SENT_FP_KEY]: seen })
        showToast(`Nexus: no pude marcar WhatsApp (${response?.error || 'error'})`, true)
        return
      }
      chrome.storage.local.remove(WA_PENDING_KEY)
      showToast('Nexus: WhatsApp marcado como enviado — salió de la cola')
    },
  )
}

function showToast(msg, isError = false) {
  const id = 'nexus-wa-toast'
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
  el.style.background = isError ? '#b91c1c' : '#25D366'
  el.textContent = msg
  setTimeout(() => el?.remove(), isError ? 10000 : 7000)
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

/** Igualdad de teléfonos WA (exacto + variantes AR 549↔54), sin last-8 flojo. */
function waDigitsMatch(a, b) {
  const da = String(a || '').replace(/\D/g, '')
  const db = String(b || '').replace(/\D/g, '')
  if (!da || !db || da.length < 8 || db.length < 8) return false
  if (da === db) return true
  const stripArMobile9 = (d) => {
    if (d.startsWith('549') && d.length >= 12) return `54${d.slice(3)}`
    if (d.startsWith('54') && !d.startsWith('549') && d.length >= 11) {
      return `549${d.slice(2)}`
    }
    return d
  }
  const sa = new Set([da, stripArMobile9(da)])
  const sb = new Set([db, stripArMobile9(db)])
  for (const x of sa) {
    if (sb.has(x)) return true
  }
  if (da.length >= 10 && db.length >= 10 && da.slice(-10) === db.slice(-10)) return true
  return false
}

let _waCfgCache = null
let _waCfgCacheAt = 0

async function getWaExtConfigLocal() {
  if (_waCfgCache && Date.now() - _waCfgCacheAt < 60_000) return _waCfgCache
  try {
    const stored = await chrome.storage.local.get(['nexusWaExtConfig'])
    _waCfgCache = stored?.nexusWaExtConfig || {}
    _waCfgCacheAt = Date.now()
  } catch {
    _waCfgCache = {}
  }
  return _waCfgCache
}

async function pickWatchBatchWithCursor(watchList, batchSize) {
  const list = (watchList || []).filter(Boolean)
  if (!list.length) return []
  const size = Math.max(1, Math.min(Number(batchSize) || WA_OPEN_BATCH_DEFAULT, list.length))
  try {
    const stored = await chrome.storage.local.get([WA_WATCH_CURSOR_KEY])
    let cursor = Number(stored?.[WA_WATCH_CURSOR_KEY] || 0) % list.length
    const batch = []
    for (let i = 0; i < size; i += 1) {
      batch.push(list[(cursor + i) % list.length])
    }
    cursor = (cursor + size) % list.length
    await chrome.storage.local.set({ [WA_WATCH_CURSOR_KEY]: cursor })
    return batch
  } catch {
    return list.slice(0, size)
  }
}

async function loadWaWatchList() {
  try {
    const stored = await chrome.storage.local.get([WA_WATCH_LIST_KEY])
    const raw = Array.isArray(stored?.[WA_WATCH_LIST_KEY]) ? stored[WA_WATCH_LIST_KEY] : []
    const now = Date.now()
    return raw
      .map((x) => ({
        prospectId: Number(x?.prospectId || 0) || 0,
        phone: String(x?.phone || '').replace(/\D/g, ''),
        name: String(x?.name || '').trim(),
        outboundText: String(x?.outboundText || '').trim(),
        since: Number(x?.since || 0) || 0,
      }))
      .filter((x) => x.phone.length >= 8 || x.prospectId)
      .filter((x) => !x.since || now - x.since < WA_WATCH_TTL_MS)
  } catch {
    return []
  }
}

async function upsertWaWatchTarget({ prospectId, phoneDigits, prospectName, outboundText }) {
  const phone = String(phoneDigits || '').replace(/\D/g, '')
  const pid = Number(prospectId || 0) || 0
  if (!pid && phone.length < 8) return
  const list = await loadWaWatchList()
  const next = list.filter(
    (x) =>
      !(pid && Number(x.prospectId) === pid) &&
      !(phone.length >= 8 && x.phone && waDigitsMatch(x.phone, phone)),
  )
  next.unshift({
    prospectId: pid,
    phone,
    name: String(prospectName || '').trim(),
    outboundText: String(outboundText || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 240),
    since: Date.now(),
  })
  await chrome.storage.local.set({
    [WA_WATCH_LIST_KEY]: next.slice(0, WA_WATCH_MAX),
    nexusWaWatchUntil: Date.now() + 2 * 60 * 60 * 1000,
  })
}

/**
 * Match de una fila de la lista contra la watch list de Nexus (solo números/nombres nuestros).
 */
function matchWatchTarget(watchList, { title, phone }) {
  const titleDigits = String(title || '').replace(/\D/g, '')
  const rowPhone = String(phone || '').replace(/\D/g, '') || titleDigits
  for (const w of watchList || []) {
    if (w.phone.length >= 8 && rowPhone.length >= 8 && waDigitsMatch(rowPhone, w.phone)) {
      return w
    }
    if (w.phone.length >= 8 && titleDigits.length >= 8 && waDigitsMatch(titleDigits, w.phone)) {
      return w
    }
    if (w.name && title && (namesMatchStrong(w.name, title) || namesMatchLoose(w.name, title))) {
      return w
    }
  }
  return null
}

function normalizeWaCompare(s) {
  return String(s || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

/** Match fuerte de nombre de contacto (evita “Ana” vs “Anabela”). */
function namesMatchStrong(a, b) {
  const na = normalizeWaCompare(a).replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim()
  const nb = normalizeWaCompare(b).replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim()
  if (!na || !nb) return false
  if (na === nb) return true
  if (na.length >= 5 && nb.length >= 5 && (na.includes(nb) || nb.includes(na))) return true
  const pa = na.split(' ').filter((p) => p.length >= 2)
  const pb = nb.split(' ').filter((p) => p.length >= 2)
  if (pa.length >= 2 && pb.length >= 2) {
    const setB = new Set(pb)
    const overlap = pa.filter((p) => setB.has(p)).length
    if (overlap >= 2) return true
  }
  return false
}

/** Match laxo (paso 1): mismo primer nombre ≥3 letras, solo con watch sticky activo. */
function namesMatchLoose(a, b) {
  if (namesMatchStrong(a, b)) return true
  const pa = normalizeWaCompare(a)
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter((p) => p.length >= 3)
  const pb = normalizeWaCompare(b)
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter((p) => p.length >= 3)
  if (!pa.length || !pb.length) return false
  return pa.some((p) => pb.some((q) => p === q || (p.length >= 4 && q.startsWith(p)) || (q.length >= 4 && p.startsWith(q))))
}

async function isWaWatchActive() {
  try {
    const watchList = await loadWaWatchList()
    if (watchList.length) return true
    const stored = await chrome.storage.local.get([
      'nexusWaWatchUntil',
      'nexusWaLastOutboundAt',
      'nexusWaLastProspectId',
      'nexusWaLastChatPhone',
      WA_PENDING_KEY,
    ])
    const until = Number(stored?.nexusWaWatchUntil || 0)
    if (until && Date.now() < until) return true
    const lastOut = Number(stored?.nexusWaLastOutboundAt || 0)
    if (lastOut && Date.now() - lastOut < 2 * 60 * 60 * 1000) return true
    if (stored?.[WA_PENDING_KEY]?.prospectId) return true
    if (stored?.nexusWaLastProspectId && stored?.nexusWaLastChatPhone) return true
    return false
  } catch {
    return false
  }
}

function getActiveChatTitle() {
  const header =
    document.querySelector('#main header span[title]') ||
    document.querySelector('#main header [data-testid="conversation-info-header-chat-title"]') ||
    document.querySelector('#main header span[dir="auto"]') ||
    document.querySelector('header span[title]')
  return String(header?.getAttribute('title') || header?.textContent || '')
    .replace(/\s+/g, ' ')
    .trim()
}

async function getWatchedProspectName() {
  try {
    const stored = await chrome.storage.local.get(['nexusWaLastProspectName', WA_PENDING_KEY])
    const fromPending = String(stored?.[WA_PENDING_KEY]?.prospectName || '').trim()
    const fromLast = String(stored?.nexusWaLastProspectName || '').trim()
    return fromPending || fromLast
  } catch {
    return ''
  }
}

/** Chat abierto = el vigilado (teléfono en DOM/header O nombre del prospecto). */
async function activeChatMatchesWatched(hints) {
  const watchedPhone = String(hints?.phone || '').replace(/\D/g, '')
  const activePhone = extractActiveChatPhone()
  if (watchedPhone.length >= 8 && activePhone && waDigitsMatch(activePhone, watchedPhone)) {
    return true
  }
  const title = getActiveChatTitle()
  const titleDigits = title.replace(/\D/g, '')
  if (watchedPhone.length >= 8 && titleDigits.length >= 8 && waDigitsMatch(titleDigits, watchedPhone)) {
    return true
  }
  const knownName = await getWatchedProspectName()
  if (knownName.length >= 3 && title) {
    if (namesMatchStrong(knownName, title)) return true
    // Con watch activo, match laxo de nombre (Juan ↔ Juan Pérez).
    if ((await isWaWatchActive()) && namesMatchLoose(knownName, title)) return true
  }
  // Chat abierto sin dígitos en header: si hay burbujas con el teléfono vigilado, matchea.
  if (watchedPhone.length >= 8) {
    const ordered = collectOrderedWaMessages()
    if (ordered.some((m) => m.phone && waDigitsMatch(m.phone, watchedPhone))) return true
  }
  return false
}

/**
 * Limpia preview de lista: quita “Tú:”, ticks y placeholders multimedia.
 */
function cleanWaPreviewText(raw) {
  let t = String(raw || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!t) return ''
  t = t.replace(/^[\u2713\u2714✓✔]+\s*/g, '')
  t = t.replace(/^(t[uú]|you|vos)\s*:\s*/i, '')
  t = t.replace(/^draft\s*:\s*/i, '')
  t = t.replace(/\s+/g, ' ').trim()
  if (
    /^(foto|photo|imagen|image|gif|sticker|audio|video|documento|document|contact card|tarjeta de contacto|gif omitido|multimedia omitido|mensaje eliminado|this message was deleted)$/i.test(
      t,
    )
  ) {
    return ''
  }
  return t
}

/**
 * Inbounds en el chat abierto después del último outbound (o el último inbound si no hay out).
 */
function extractInboundAfterOurOutbound() {
  const ordered = collectOrderedWaMessages()
  if (!ordered.length) return []
  let lastOut = -1
  for (let i = 0; i < ordered.length; i += 1) {
    if (ordered[i].kind === 'out') lastOut = i
  }
  // Si el último mensaje es nuestro, no hay respuesta nueva a registrar.
  if (ordered[ordered.length - 1]?.kind === 'out') return []

  /** @type {{ text: string, phone: string }[]} */
  const out = []
  if (lastOut < 0) {
    const last = ordered[ordered.length - 1]
    if (last?.kind === 'in' && last.text) {
      out.push({ text: last.text.slice(0, 500), phone: last.phone || '' })
    }
    return out
  }
  for (const row of ordered.slice(lastOut + 1)) {
    if (row.kind !== 'in' || !row.text) continue
    out.push({ text: row.text.slice(0, 500), phone: row.phone || '' })
  }
  return out.slice(-3)
}

/**
 * Fallback: si classify falló para inbound, busca textos tras nuestro outbound conocido.
 */
async function extractInboundViaOutboundTextFallback(watch) {
  const ours = String(watch?.outboundText || '').replace(/\s+/g, ' ').trim()
  const ordered = collectOrderedWaMessages()
  if (!ordered.length) {
    // Último recurso: scrape de textos visibles en #main.
    const texts = scrapeAllVisibleMessageTexts()
    if (!ours || ours.length < 12) {
      const last = texts[texts.length - 1]
      if (last && !(await isEchoOfOurOutbound(last, watch?.phone, ours))) {
        return [{ text: last.slice(0, 500), phone: watch?.phone || '' }]
      }
      return []
    }
    return textsAfterOursLocal(texts, ours)
      .filter((t) => t && !waTextsLookSame(t, ours))
      .slice(-2)
      .map((t) => ({ text: t.slice(0, 500), phone: watch?.phone || '' }))
  }
  if (ordered[ordered.length - 1]?.kind === 'out') return []
  return extractInboundAfterOurOutbound()
}

function watchInboundDomMutations() {
  let timer = null
  const schedule = () => {
    if (timer) return
    timer = window.setTimeout(() => {
      timer = null
      void pollWhatsAppInbound('mutation')
    }, 500)
  }
  try {
    const obs = new MutationObserver(schedule)
    obs.observe(document.body, { childList: true, subtree: true })
  } catch {
    /* ignore */
  }
}

const WA_INBOUND_SEEN_KEY = 'nexusWaInboundSeen'
const WA_INBOUND_MAX_SEEN = 400

let waUnreadCaptureBusy = false

async function ensureWatchListSeeded() {
  const existing = await loadWaWatchList()
  if (existing.length) return existing
  try {
    const stored = await chrome.storage.local.get([
      WA_PENDING_KEY,
      'nexusWaLastChatPhone',
      'nexusWaLastProspectId',
      'nexusWaLastProspectName',
      'nexusWaLastOutboundText',
      'nexusWaLastOutboundAt',
    ])
    const phone = String(
      stored?.[WA_PENDING_KEY]?.phoneDigits || stored?.nexusWaLastChatPhone || '',
    ).replace(/\D/g, '')
    const pid = Number(
      stored?.[WA_PENDING_KEY]?.prospectId || stored?.nexusWaLastProspectId || 0,
    )
    const lastOut = Number(stored?.nexusWaLastOutboundAt || 0)
    const recent = lastOut && Date.now() - lastOut < WA_WATCH_TTL_MS
    if (!(phone.length >= 8 || pid) || (!stored?.[WA_PENDING_KEY] && !recent && !pid)) {
      return existing
    }
    await upsertWaWatchTarget({
      prospectId: pid,
      phoneDigits: phone,
      prospectName:
        stored?.nexusWaLastProspectName || stored?.[WA_PENDING_KEY]?.prospectName || '',
      outboundText:
        stored?.nexusWaLastOutboundText || stored?.[WA_PENDING_KEY]?.message || '',
    })
    return loadWaWatchList()
  } catch {
    return existing
  }
}

async function requestWaStoreRead(watchList) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(
        {
          type: 'NEXUS_WA_STORE_READ',
          watchList: (watchList || []).map((w) => ({
            prospectId: w.prospectId,
            phone: w.phone,
            name: w.name,
            outboundText: w.outboundText,
          })),
        },
        (res) => {
          void chrome.runtime.lastError
          resolve(res || { ok: false, rows: [] })
        },
      )
    } catch {
      resolve({ ok: false, rows: [] })
    }
  })
}

async function pollWhatsAppInbound(_reason) {
  // Prioridad: Store interno (cualquier chat abierto / ninguno).
  // Fallback: lista DOM → abrir solo si pestaña en background.
  if (!isWhatsAppLoggedIn()) return { ok: false, reason: 'not_logged_in' }

  /** @type {{ phone: string, text: string, prospectId?: number, source?: string }[]} */
  const candidates = []
  const hints = await getWaChatHints()
  let watchList = await ensureWatchListSeeded()
  const waCfg =
    (await chrome.storage.local.get(['nexusWaExtConfig']).catch(() => ({})))?.nexusWaExtConfig ||
    (await getWaExtConfigLocal())
  _waCfgCache = waCfg
  _waCfgCacheAt = Date.now()
  if (!watchList.length && !(await isWaWatchActive())) {
    return { ok: true, candidates: 0, watching: false }
  }

  const label = watchList
    .slice(0, 3)
    .map((w) => w.name || w.phone || `#${w.prospectId}`)
    .filter(Boolean)
    .join(' · ')

  // 0) Store — no depende del chat abierto ni del foco.
  let storeDiag = null
  const storeRes = await requestWaStoreRead(watchList)
  storeDiag = storeRes?.diag || null
  if (storeRes?.ok && Array.isArray(storeRes.rows)) {
    for (const row of storeRes.rows) {
      if (!row?.text) continue
      if (await isEchoOfOurOutbound(row.text, row.phone)) continue
      candidates.push({ ...row, source: row.source || 'wa-store' })
    }
  }

  // 1) Lista de chats (preview).
  const cfgStored = await chrome.storage.local.get(['nexusWaExtConfig']).catch(() => ({}))
  const waCfgDom = cfgStored?.nexusWaExtConfig || waCfg
  if (waCfgDom.domFallbackEnabled !== false) {
    for (const row of await scanChatListInboundPreviewsAsync(waCfgDom)) {
      if (!row.text) continue
      if (await isEchoOfOurOutbound(row.text, row.phone)) continue
      if (candidates.some((c) => normalizeWaCompare(c.text) === normalizeWaCompare(row.text))) continue
      candidates.push({ ...row, source: row.source || 'chat-list' })
    }
  }

  // 2) Abrir chat solo si Store falló y la pestaña está en background.
  const storeWorks = Boolean(storeRes?.ok && storeDiag && storeDiag.source && !storeDiag.error)
  if (!candidates.length && !storeWorks && waCfgDom.quietOpenEnabled !== false) {
    for (const msg of await openWatchedChatsAndReadInbound(watchList, waCfgDom)) {
      if (!msg?.text) continue
      if (await isEchoOfOurOutbound(msg.text, msg.phone)) continue
      if (candidates.some((c) => normalizeWaCompare(c.text) === normalizeWaCompare(msg.text))) continue
      candidates.push({ ...msg, source: msg.source || 'opened-chat' })
    }
  }

  // 3) Si el chat ya abierto matchea, leer burbujas DOM.
  if (await activeChatMatchesAnyWatch(watchList, hints)) {
    const activeWatch =
      matchWatchTarget(watchList, {
        title: getActiveChatTitle(),
        phone: extractActiveChatPhone() || hints.phone || '',
      }) || null
    for (const msg of extractInboundAfterOurOutbound()) {
      if (!msg?.text) continue
      if (await isEchoOfOurOutbound(msg.text, hints.phone || activeWatch?.phone)) continue
      if (candidates.some((c) => normalizeWaCompare(c.text) === normalizeWaCompare(msg.text))) {
        continue
      }
      candidates.push({
        phone: activeWatch?.phone || hints.phone || msg.phone || extractActiveChatPhone() || '',
        text: msg.text,
        prospectId: activeWatch?.prospectId || hints.prospectId || undefined,
        source: 'open-chat',
      })
    }
  }

  const storeLabel = storeDiag
    ? `store:${storeDiag.source || storeDiag.error || '?'} c:${storeDiag.chats || 0} m:${storeDiag.matched || 0} in:${storeDiag.inbound || 0}`
    : 'store:?'
  showNexusWaWatchBadge(
    `Nexus vigila ${watchList.length || 0}: ${label || '…'} · ${storeLabel} · cand:${candidates.length}`,
  )

  let reported = 0
  const reportCap = Math.min(
    Number(waCfgDom.domReportBatchSize) || WA_REPORT_BATCH_DEFAULT,
    WA_WATCH_MAX,
  )
  for (const row of candidates.slice(0, reportCap)) {
    if (await isEchoOfOurOutbound(row.text, row.phone)) continue
    const phone = row.phone || hints.phone || ''
    const watched =
      Boolean(row.prospectId) ||
      watchList.some(
        (w) =>
          (phone && w.phone && waDigitsMatch(phone, w.phone)) ||
          (row.prospectId && w.prospectId && Number(row.prospectId) === Number(w.prospectId)),
      ) ||
      (hints.phone && phone && waDigitsMatch(phone, hints.phone)) ||
      (hints.prospectId && row.prospectId && Number(hints.prospectId) === Number(row.prospectId))
    if (!watched) continue
    await reportWhatsAppInboundIfNew(
      phone || hints.phone || '0',
      row.text,
      row.prospectId || hints.prospectId || undefined,
    )
    reported += 1
  }

  // Telemetría al background (sin texto de mensajes).
  try {
    chrome.runtime.sendMessage(
      {
        type: 'NEXUS_WA_TELEMETRY',
        store_ok: Boolean(storeDiag && storeDiag.source && !storeDiag.error),
        store_source: storeDiag?.source || null,
        store_error: storeDiag?.error || null,
        chats: storeDiag?.chats ?? null,
        matched: storeDiag?.matched ?? null,
        inbound: storeDiag?.inbound ?? null,
        candidates: candidates.length,
        reported,
        reason: String(_reason || 'poll'),
      },
      () => {
        void chrome.runtime.lastError
      },
    )
  } catch {
    /* ignore */
  }

  return {
    ok: true,
    candidates: candidates.length,
    reported,
    watching: true,
    watchTargets: watchList.length,
    store: storeDiag,
  }
}

/** Cooldown de open en storage (sobrevive reload de /send?phone=). */
const WA_QUIET_OPEN_COOLDOWN_MS = 90 * 1000
const WA_QUIET_OPEN_KEY = 'nexusWaQuietOpenAt'

async function isWaTabBackgroundSafe() {
  // Si el usuario está mirando esta pestaña, NO abrir/cambiar chat (roba foco).
  try {
    if (!document.hidden && document.hasFocus()) return false
    if (document.visibilityState === 'visible' && document.hasFocus()) return false
  } catch {
    /* ignore */
  }
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: 'NEXUS_WA_IS_TAB_FOCUSED' }, (res) => {
        void chrome.runtime.lastError
        // focused/active → no es seguro abrir
        resolve(!res?.focused)
      })
    } catch {
      resolve(document.hidden === true)
    }
  })
}

async function getQuietOpenMap() {
  try {
    const st = await chrome.storage.local.get([WA_QUIET_OPEN_KEY])
    return st?.[WA_QUIET_OPEN_KEY] && typeof st[WA_QUIET_OPEN_KEY] === 'object'
      ? st[WA_QUIET_OPEN_KEY]
      : {}
  } catch {
    return {}
  }
}

async function markQuietOpen(key) {
  const map = await getQuietOpenMap()
  map[key] = Date.now()
  try {
    await chrome.storage.local.set({ [WA_QUIET_OPEN_KEY]: map })
  } catch {
    /* ignore */
  }
}

async function recentlyQuietOpened(key) {
  const map = await getQuietOpenMap()
  const at = Number(map[key] || 0)
  return Boolean(at && Date.now() - at < WA_QUIET_OPEN_COOLDOWN_MS)
}

async function activeChatMatchesAnyWatch(watchList, hints) {
  if (await activeChatMatchesWatched(hints)) return true
  const title = getActiveChatTitle()
  const phone = extractActiveChatPhone()
  for (const w of watchList || []) {
    if (matchWatchTarget([w], { title, phone })) return true
    if (w.phone && phone && waDigitsMatch(phone, w.phone)) return true
    if (w.name && title && (namesMatchStrong(w.name, title) || namesMatchLoose(w.name, title))) {
      return true
    }
  }
  return false
}

function findWatchedRowInList(watch, cfg) {
  const root = chatListRoot(cfg)
  if (!root || !watch) return null
  const items = root.querySelectorAll(
    '[data-testid="cell-frame-container"], div[role="listitem"], div[tabindex="-1"]',
  )
  for (const item of items) {
    const titleEl =
      item.querySelector('[data-testid="cell-frame-title"] span[title]') ||
      item.querySelector('[data-testid="cell-frame-title"] span') ||
      item.querySelector('span[title]')
    const title = (titleEl?.getAttribute('title') || titleEl?.textContent || '').trim()
    const phone = digPhoneFromElement(item) || title.replace(/\D/g, '')
    if (matchWatchTarget([watch], { title, phone })) return item
  }
  return null
}

async function findWatchedRowInListWithScroll(watch, cfg) {
  let row = findWatchedRowInList(watch, cfg)
  if (row) return row
  if (cfg?.domScrollSearchEnabled === false) return null
  const root = chatListRoot(cfg)
  if (!root) return null
  const scrollEl =
    root.querySelector('[data-testid="chat-list"]') ||
    root.querySelector('[role="grid"]') ||
    root.querySelector('[tabindex="-1"]') ||
    root
  const startTop = scrollEl.scrollTop || 0
  for (let i = 0; i < 10; i += 1) {
    scrollEl.scrollTop = (scrollEl.scrollTop || 0) + Math.max(180, (scrollEl.clientHeight || 400) * 0.75)
    await sleep(140)
    row = findWatchedRowInList(watch, cfg)
    if (row) {
      scrollEl.scrollTop = startTop
      return row
    }
  }
  scrollEl.scrollTop = startTop
  return null
}

function clickChatListRow(item) {
  if (!item) return false
  const clickable =
    item.querySelector('[data-testid="cell-frame-container"]') ||
    item.closest?.('[data-testid="cell-frame-container"]') ||
    item
  try {
    clickable.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }))
    clickable.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }))
    clickable.click()
    return true
  } catch {
    return false
  }
}

/**
 * Pide al background navegar a /send?phone= SOLO si la pestaña no está enfocada.
 */
function requestQuietOpenChat(phoneDigits) {
  const phone = String(phoneDigits || '').replace(/\D/g, '')
  if (phone.length < 8) return Promise.resolve({ ok: false })
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: 'NEXUS_WA_OPEN_CHAT_QUIET', phoneDigits: phone }, (res) => {
        void chrome.runtime.lastError
        resolve(res || { ok: false })
      })
    } catch {
      resolve({ ok: false })
    }
  })
}

function readInboundFromOpenWatch(w) {
  /** @type {{ phone: string, text: string, prospectId?: number, source?: string }[]} */
  const rows = []
  for (const msg of extractInboundAfterOurOutbound()) {
    if (!msg?.text) continue
    rows.push({
      phone: w.phone || msg.phone || '',
      text: msg.text,
      prospectId: w.prospectId || undefined,
      source: 'quiet-open',
    })
  }
  return rows
}

/**
 * Lee inbound del chat vigilado. Solo abre/cambia chat si WA está en background.
 */
async function openWatchedChatsAndReadInbound(watchList, cfg) {
  /** @type {{ phone: string, text: string, prospectId?: number, source?: string }[]} */
  const out = []
  if (waUnreadCaptureBusy) return out
  const waCfg = cfg || (await getWaExtConfigLocal())
  const targets = (watchList || []).filter((w) => w.phone?.length >= 8 || w.prospectId || w.name)
  if (!targets.length) return out

  waUnreadCaptureBusy = true
  try {
    const canOpen = await isWaTabBackgroundSafe()
    const openBatch = Math.min(
      Number(waCfg.domOpenBatchSize) || WA_OPEN_BATCH_DEFAULT,
      targets.length,
    )
    const batch = await pickWatchBatchWithCursor(targets, openBatch)
    if (!canOpen) {
      showNexusWaWatchBadge(
        `Nexus vigila ${targets.length}: ${batch[0]?.name || '…'} (WA en foco: solo leo, no cambio chat)`,
      )
    }

    for (const w of batch) {
      const key = String(w.prospectId || w.phone || w.name || '')
      const alreadyOpen = await activeChatMatchesAnyWatch(
        [w],
        { phone: w.phone || '', prospectId: w.prospectId || 0 },
      )

      if (!alreadyOpen && canOpen) {
        if (await recentlyQuietOpened(key)) {
          // Ya intentamos abrir hace poco; no re-navegar (evita loop + foco).
        } else {
          await markQuietOpen(key)
          const row = (await findWatchedRowInListWithScroll(w, waCfg)) || findWatchedRowInList(w, waCfg)
          let opened = false
          if (row) {
            opened = clickChatListRow(row)
            if (opened) await sleep(1200)
          }
          if (!opened && w.phone && w.phone.length >= 8) {
            const nav = await requestQuietOpenChat(w.phone)
            if (nav?.ok && !nav?.skipped) await sleep(3200)
          }
        }
      }

      const matched =
        (await activeChatMatchesAnyWatch([w], { phone: w.phone || '', prospectId: w.prospectId || 0 })) ||
        (w.name &&
          getActiveChatTitle() &&
          (namesMatchStrong(w.name, getActiveChatTitle()) ||
            namesMatchLoose(w.name, getActiveChatTitle())))

      if (!matched) continue

      let got = readInboundFromOpenWatch(w)
      if (!got.length) {
        await sleep(1500)
        got = readInboundFromOpenWatch(w)
      }
      if (!got.length) {
        for (const msg of await extractInboundViaOutboundTextFallback(w)) {
          if (!msg?.text) continue
          got.push({
            phone: w.phone || msg.phone || '',
            text: msg.text,
            prospectId: w.prospectId || undefined,
            source: 'quiet-open-fallback',
          })
        }
      }
      for (const msg of got) {
        if (await isEchoOfOurOutbound(msg.text, w.phone, w.outboundText)) continue
        out.push(msg)
      }
    }
  } finally {
    waUnreadCaptureBusy = false
  }
  return out
}

/** @deprecated compat */
async function openUnreadChatsAndCaptureInbound() {
  const watchList = await loadWaWatchList()
  return openWatchedChatsAndReadInbound(watchList)
}

async function openKnownPhoneChatAndCapture() {
  const watchList = await loadWaWatchList()
  return openWatchedChatsAndReadInbound(watchList)
}

async function aggressiveWatchKnownProspect() {
  const watchList = await loadWaWatchList()
  return openWatchedChatsAndReadInbound(watchList)
}

function chatListRoot(cfg) {
  const ids = Array.isArray(cfg?.domChatListTestIds) ? cfg.domChatListTestIds : []
  for (const id of ids) {
    const el = document.querySelector(`[data-testid="${id}"]`)
    if (!el) continue
    if (id === 'chat-list' || el.querySelector('[role="listitem"], [data-testid="cell-frame-container"]')) {
      return el.closest('#pane-side, #side') || el
    }
  }
  return (
    document.querySelector('#pane-side') ||
    document.querySelector('#side') ||
    document.querySelector('[data-testid="chat-list"]') ||
    document.querySelector('div[aria-label*="Lista de chats" i]') ||
    document.querySelector('div[aria-label*="Chat list" i]')
  )
}

/** Unread en lista WA (selectores varían; icon-unread es el actual). */
function rowHasUnread(item, cfg) {
  if (!item) return false
  const testIds = Array.isArray(cfg?.domUnreadTestIds) ? cfg.domUnreadTestIds : []
  for (const id of testIds) {
    if (item.querySelector(`[data-testid="${id}"]`)) return true
  }
  return Boolean(
    item.querySelector('[data-testid="icon-unread-count"]') ||
      item.querySelector('[data-testid="icon-unread"]') ||
      item.querySelector('[data-testid="unread-count"]') ||
      item.querySelector('[data-testid*="unread" i]') ||
      item.querySelector('span[aria-label*="no leído" i]') ||
      item.querySelector('span[aria-label*="unread" i]') ||
      item.querySelector('span[aria-label*="mensaje no leído" i]') ||
      item.querySelector('span[aria-label*="mensajes no leídos" i]'),
  )
}

function findUnreadChatListItems() {
  const root = chatListRoot()
  if (!root) return []
  const items = [
    ...root.querySelectorAll(
      '[data-testid="cell-frame-container"], div[role="listitem"], div[tabindex="-1"]',
    ),
  ]
  return items.filter((item) => rowHasUnread(item))
}

async function getWaChatHints() {
  try {
    const stored = await chrome.storage.local.get([
      WA_PENDING_KEY,
      'nexusWaLastChatPhone',
      'nexusWaLastProspectId',
    ])
    const pending = stored?.[WA_PENDING_KEY]
    const phone = String(
      pending?.phoneDigits || stored?.nexusWaLastChatPhone || '',
    ).replace(/\D/g, '')
    const prospectId = Number(pending?.prospectId || stored?.nexusWaLastProspectId || 0) || 0
    return { phone, prospectId }
  } catch {
    return { phone: '', prospectId: 0 }
  }
}

async function isEchoOfOurOutbound(text, phoneHint, outboundHint) {
  const t = cleanWaPreviewText(text)
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  // Respuestas cortas reales ("ok", "dale", "sí") no deben caer como eco.
  if (!t || t.length < 2) return false
  if (/^t[uú]:/i.test(t) || /^you:/i.test(t) || /^vos:/i.test(t)) return true
  try {
    const stored = await chrome.storage.local.get([
      WA_PENDING_KEY,
      'nexusWaLastOutboundText',
      'nexusWaSuppressInboundUntil',
      WA_WATCH_LIST_KEY,
    ])
    const suppressUntil = Number(stored?.nexusWaSuppressInboundUntil || 0)
    const inSuppress = Boolean(suppressUntil && Date.now() < suppressUntil)
    const candidates = [
      String(outboundHint || ''),
      String(stored?.[WA_PENDING_KEY]?.message || stored?.[WA_PENDING_KEY]?.messagePrefix || ''),
      String(stored?.nexusWaLastOutboundText || ''),
    ]
    const phone = String(phoneHint || '').replace(/\D/g, '')
    const watchList = Array.isArray(stored?.[WA_WATCH_LIST_KEY]) ? stored[WA_WATCH_LIST_KEY] : []
    for (const w of watchList) {
      if (phone && w?.phone && waDigitsMatch(phone, w.phone) && w.outboundText) {
        candidates.push(String(w.outboundText))
      }
    }
    for (const raw of candidates) {
      const ours = cleanWaPreviewText(raw).replace(/\s+/g, ' ').trim().toLowerCase()
      if (ours.length < 8) continue
      if (waTextsLookSame(t, ours)) return true
      // Solo en ventana post-envío, y solo si el preview es lo bastante largo
      // (evitar que "ok"/"sí" dentro del outbound anulen un inbound real).
      if (inSuppress && ours.length >= 12 && t.length >= 16) {
        if (ours.includes(t) || t.includes(ours.slice(0, Math.min(32, ours.length)))) return true
      }
    }
  } catch {
    /* ignore */
  }
  return false
}

function waTextsLookSame(a, b) {
  const x = String(a || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  const y = String(b || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  if (!x || !y) return false
  if (x === y) return true
  const n = Math.min(48, x.length, y.length)
  if (n >= 16 && (x.slice(0, n) === y.slice(0, n) || x.includes(y.slice(0, n)) || y.includes(x.slice(0, n)))) {
    return true
  }
  if (x.length >= 20 && y.includes(x.slice(0, 20))) return true
  if (y.length >= 20 && x.includes(y.slice(0, 20))) return true
  const shorter = x.length <= y.length ? x : y
  const longer = x.length <= y.length ? y : x
  if (shorter.length >= 24 && longer.includes(shorter)) return true
  return false
}

function normalizeWaInboundFingerprint(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/^(t[uú]|you|vos)\s*:\s*/i, '')
    .replace(/[^\p{L}\p{N}]+/gu, '')
    .slice(0, 240)
}

async function reportWhatsAppInboundIfNew(phone, text, prospectIdHint) {
  const pid = Number(prospectIdHint || 0) || 0
  const compact = normalizeWaInboundFingerprint(text)
  const fp = `wa-in:${pid || phone}:${simpleHash(compact || text.slice(0, 200))}`
  const compactKey = compact.length >= 8 ? `wa-in:${pid || phone}:c:${compact.slice(0, 80)}` : ''
  const stored = await chrome.storage.local.get(WA_INBOUND_SEEN_KEY)
  const seen = stored?.[WA_INBOUND_SEEN_KEY] || {}
  if (seen[fp] || (compactKey && seen[compactKey])) return

  // Lock optimista: marcar YA para que polls paralelos no disparen N POSTs.
  seen[fp] = Date.now()
  if (compactKey) seen[compactKey] = Date.now()
  await chrome.storage.local.set({ [WA_INBOUND_SEEN_KEY]: seen })

  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      {
        type: 'NEXUS_WHATSAPP_INBOUND_DETECTED',
        phoneDigits: phone,
        message: text,
        whatsappMessageId: null,
        prospectId: pid || null,
      },
      (response) => {
        if (chrome.runtime.lastError) {
          delete seen[fp]
          if (compactKey) delete seen[compactKey]
          chrome.storage.local.set({ [WA_INBOUND_SEEN_KEY]: seen })
          showToast(`Nexus WA inbound: ${chrome.runtime.lastError.message}`, true)
          resolve()
          return
        }
        if (!response?.ok) {
          const err = String(response?.error || 'error')
          // Duplicado / race: no asustar al SDR.
          if (/ya estaba|duplicate|race/i.test(err)) {
            resolve()
            return
          }
          delete seen[fp]
          if (compactKey) delete seen[compactKey]
          chrome.storage.local.set({ [WA_INBOUND_SEEN_KEY]: seen })
          showToast(`Nexus WA: no registró inbound — ${err.slice(0, 100)}`, true)
          resolve()
          return
        }
        if (response.echoIgnored) {
          delete seen[fp]
          if (compactKey) delete seen[compactKey]
          chrome.storage.local.set({ [WA_INBOUND_SEEN_KEY]: seen })
          showNexusWaWatchBadge('Nexus: el texto parecía eco de tu envío (ignorado)')
          resolve()
          return
        }
        const keys = Object.keys(seen)
        if (keys.length > WA_INBOUND_MAX_SEEN) {
          keys
            .sort((a, b) => Number(seen[a] || 0) - Number(seen[b] || 0))
            .slice(0, keys.length - WA_INBOUND_MAX_SEEN)
            .forEach((k) => delete seen[k])
          chrome.storage.local.set({ [WA_INBOUND_SEEN_KEY]: seen })
        }
        if (response.calendarReconnectRequired) {
          const msg =
            response.operatorMessage ||
            'Google Calendar necesita reconexión. Andá a Configuración → Integraciones.'
          showToast(`Nexus: ${String(msg).slice(0, 160)}`, true)
        }
        // Inbound OK: sin toast (Nexus actualiza la cola en la app).
        resolve()
      },
    )
  })
}

function phoneFromWhatsAppDataId(raw) {
  const m = String(raw || '').match(/(\d{8,15})@(?:c\.us|s\.whatsapp\.net)/i)
  return m ? m[1] : ''
}

function digPhoneFromElement(root) {
  if (!root) return ''
  const own = phoneFromWhatsAppDataId(root.getAttribute?.('data-id'))
  if (own) return own
  const withId = root.querySelectorAll?.('[data-id]') || []
  for (const el of withId) {
    const p = phoneFromWhatsAppDataId(el.getAttribute('data-id'))
    if (p) return p
  }
  return ''
}

/**
 * Lista de chats: lee previews SIN abrir el chat.
 * Solo filas que matchean la watch list de Nexus (números/nombres con los que hablamos).
 */
async function scanChatListInboundPreviewsAsync(cfg) {
  /** @type {{ phone: string, text: string, prospectId?: number, source?: string }[]} */
  const out = []
  const waCfg = cfg || (await getWaExtConfigLocal())
  const root = chatListRoot(waCfg)
  if (!root) return out

  let watchList = await loadWaWatchList()
  if (!watchList.length) {
    // Fallback sticky único (compat).
    const hints = await getWaChatHints()
    const knownName = await getWatchedProspectName()
    if (hints.phone || hints.prospectId) {
      watchList = [
        {
          prospectId: hints.prospectId || 0,
          phone: hints.phone || '',
          name: knownName,
          outboundText: '',
          since: Date.now(),
        },
      ]
    }
  }
  if (!watchList.length) return out

  const items = root.querySelectorAll(
    '[data-testid="cell-frame-container"], div[role="listitem"], div[tabindex="-1"]',
  )
  for (const item of items) {
    const unread = rowHasUnread(item, waCfg)

    const titleEl =
      item.querySelector('[data-testid="cell-frame-title"] span[title]') ||
      item.querySelector('[data-testid="cell-frame-title"] span') ||
      item.querySelector('span[title]')
    const title = (titleEl?.getAttribute('title') || titleEl?.textContent || '').trim()
    let phone = digPhoneFromElement(item) || title.replace(/\D/g, '')

    const watched = matchWatchTarget(watchList, { title, phone })
    // Solo chats de números/nombres que Nexus está vigilando. El resto se ignora.
    if (!watched) continue

    // Si el DOM trae dígitos que NO son el teléfono vigilado (p. ej. basura), usar el de Nexus.
    if (watched.phone) {
      if (phone.length < 8 || !waDigitsMatch(phone, watched.phone)) {
        phone = watched.phone
      }
    }

    const previewEl =
      item.querySelector('[data-testid="cell-frame-secondary"] span.selectable-text') ||
      item.querySelector('[data-testid="cell-frame-secondary"] span[dir="ltr"]') ||
      item.querySelector('[data-testid="cell-frame-secondary"] span[dir="auto"]') ||
      item.querySelector('[data-testid="cell-frame-secondary"]') ||
      null
    let preview = ''
    if (previewEl) {
      preview = cleanWaPreviewText(previewEl.innerText || previewEl.textContent || '')
    }
    if (!preview || preview.length < 1) continue

    const rawPreview = String(previewEl?.innerText || previewEl?.textContent || '')
    // Prefijo “Tú:” / “You:” = nuestro último mensaje en la lista.
    if (/^(t[uú]|you|vos)\s*:/i.test(rawPreview.trim())) continue
    if (await isEchoOfOurOutbound(preview, phone || watched.phone, watched.outboundText)) continue

    // NO usar last-msg-status como veto: en WA a veces queda el tick aunque el preview
    // ya es inbound, y sin badge unread descartábamos respuestas reales.
    // Señal extra: preview distinto al outbound vigilado (cambio en lista).
    out.push({
      phone: phone.length >= 8 ? phone : watched.phone || '',
      text: preview.slice(0, 500),
      prospectId: watched.prospectId || undefined,
      source: unread ? 'list-unread' : 'list-watched',
    })
    const scanCap = Math.min(Number(waCfg.domReportBatchSize) || WA_LIST_SCAN_MAX, WA_WATCH_MAX)
    if (out.length >= scanCap) break
  }
  return out
}

function scanChatListInboundPreviews() {
  // sync stub — usar async
  return []
}

async function resolveActiveChatPhone() {
  const direct = extractActiveChatPhone()
  if (direct) {
    try {
      await chrome.storage.local.set({ nexusWaLastChatPhone: direct })
    } catch {
      /* ignore */
    }
    return direct
  }
  // Si el chat muestra nombre (no número), usar teléfono del pending / último chat Nexus.
  try {
    const stored = await chrome.storage.local.get([WA_PENDING_KEY, 'nexusWaLastChatPhone'])
    const pending = stored?.[WA_PENDING_KEY]
    const hint = String(pending?.phoneDigits || stored?.nexusWaLastChatPhone || '').replace(
      /\D/g,
      '',
    )
    if (hint.length >= 8) return hint
  } catch {
    /* ignore */
  }
  return ''
}

function extractActiveChatPhone() {
  const main = document.querySelector('#main')
  const fromMain = digPhoneFromElement(main)
  if (fromMain) return fromMain

  // Chat activo en la lista.
  const active =
    document.querySelector('#pane-side [aria-selected="true"]') ||
    document.querySelector('#pane-side div[aria-selected="true"]') ||
    document.querySelector('#pane-side [data-testid="cell-frame-container"]:focus-within')
  const fromActive = digPhoneFromElement(active)
  if (fromActive) return fromActive

  // Cualquier data-id visible cerca del header del chat.
  for (const el of document.querySelectorAll('#main header [data-id], #main [data-id*="@c.us"]')) {
    const p = phoneFromWhatsAppDataId(el.getAttribute('data-id'))
    if (p) return p
  }

  const header =
    document.querySelector('#main header span[title]') ||
    document.querySelector('header span[title]') ||
    document.querySelector('#main header span[dir="auto"]')
  const title = (header?.getAttribute('title') || header?.textContent || '').trim()
  const fromTitle = title.replace(/\D/g, '')
  if (fromTitle.length >= 8) return fromTitle

  try {
    const u = new URL(window.location.href)
    const p = (u.searchParams.get('phone') || '').replace(/\D/g, '')
    if (p.length >= 8) return p
  } catch {
    /* ignore */
  }
  return ''
}

function classifyWaBubble(el) {
  if (!el) return ''
  const id =
    el.getAttribute?.('data-id') ||
    el.closest?.('[data-id]')?.getAttribute('data-id') ||
    ''
  // WhatsApp Web: true_ = enviado por mí, false_ = inbound.
  if (/^true_/i.test(id) || /(^|[^a-z])true_/i.test(id)) return 'out'
  if (/^false_/i.test(id) || /(^|[^a-z])false_/i.test(id)) return 'in'
  if (el.classList?.contains('message-out') || el.closest?.('.message-out')) return 'out'
  if (el.classList?.contains('message-in') || el.closest?.('.message-in')) return 'in'
  // Clases parciales (WA cambia el DOM seguido).
  const cls = `${el.className || ''} ${el.parentElement?.className || ''}`
  if (/\bmessage-out\b/i.test(cls)) return 'out'
  if (/\bmessage-in\b/i.test(cls)) return 'in'
  // Solo ticks de envío = outbound. NO usar msg-meta: también aparece en inbound.
  if (
    el.querySelector?.(
      '[data-icon="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-dblcheck-ack"]',
    )
  ) {
    return 'out'
  }
  return ''
}

function bubbleText(el) {
  const textEl =
    el.querySelector?.('span.selectable-text.copyable-text') ||
    el.querySelector?.('span.selectable-text') ||
    el.querySelector?.('[data-testid="conversation-text"]') ||
    el.querySelector?.('div.copyable-text span') ||
    null
  const raw = (textEl?.innerText || textEl?.textContent || '').replace(/\s+/g, ' ').trim()
  if (!raw) return ''
  if (/^(mensaje eliminado|this message was deleted|multimedia omitido)/i.test(raw)) return ''
  return raw
}

function collectOrderedWaMessages() {
  const main = document.querySelector('#main')
  if (!main) return []
  const nodes = main.querySelectorAll(
    [
      '[data-testid="msg-container"]',
      'div.message-in',
      'div.message-out',
      'div[data-id*="@c.us"]',
      'div[data-id*="@s.whatsapp"]',
      'div[data-id*="@lid"]',
      'div[data-id^="true_"]',
      'div[data-id^="false_"]',
      'div[data-id*="true_"]',
      'div[data-id*="false_"]',
    ].join(', '),
  )
  /** @type {{ kind: string, text: string, phone: string }[]} */
  const ordered = []
  const seenIds = new Set()
  const seenTextAt = new Set()
  for (const el of nodes) {
    const id =
      el.getAttribute?.('data-id') ||
      el.closest?.('[data-id]')?.getAttribute('data-id') ||
      ''
    if (id) {
      if (seenIds.has(id)) continue
      seenIds.add(id)
    }
    let kind = classifyWaBubble(el)
    const text = bubbleText(el)
    if (!text) continue
    // Sin true_/false_: ticks = out; sin ticks = inbound (msg-meta NO implica out).
    if (!kind) {
      const hasTicks = Boolean(
        el.querySelector?.(
          '[data-icon="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-dblcheck-ack"]',
        ),
      )
      kind = hasTicks ? 'out' : 'in'
    }
    const dedupe = `${kind}:${text.slice(0, 80)}`
    if (seenTextAt.has(dedupe)) continue
    seenTextAt.add(dedupe)
    ordered.push({
      kind,
      text,
      phone: phoneFromWhatsAppDataId(id) || '',
    })
  }
  return ordered
}

/**
 * Si el último mensaje del chat abierto es inbound → { text, phone }.
 * @deprecated Prefer extractInboundAfterOurOutbound.
 */
function extractLatestInboundIfTheySpokeLast() {
  const rows = extractInboundAfterOurOutbound()
  if (!rows.length) return null
  const last = rows[rows.length - 1]
  return { text: last.text, phone: last.phone || '' }
}

function simpleHash(s) {
  let h = 0
  const str = String(s || '')
  for (let i = 0; i < str.length; i += 1) h = (h * 31 + str.charCodeAt(i)) >>> 0
  return h.toString(16)
}
