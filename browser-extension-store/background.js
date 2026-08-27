const AUTH_KEY = 'nexusAuth'
const WA_PENDING_KEY = 'nexusWaPendingSend'
const DEFAULT_API = 'https://api-production-21aa.up.railway.app'
const NEXUS_ORIGINS = ['https://nexus.costguard.com.ar/*']

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function syncAuth({ token, apiBaseUrl, companyId }) {
  const auth = {
    token: String(token || '').trim(),
    apiBaseUrl: String(apiBaseUrl || DEFAULT_API).replace(/\/+$/, ''),
    companyId: companyId ? Number(companyId) : null,
    syncedAt: Date.now(),
  }
  await chrome.storage.session.set({ [AUTH_KEY]: auth })
  return { ok: true }
}

async function getAuth() {
  const stored = await chrome.storage.session.get(AUTH_KEY)
  const auth = stored?.[AUTH_KEY]
  if (!auth?.token) return null
  return {
    token: auth.token,
    apiBaseUrl: (auth.apiBaseUrl || DEFAULT_API).replace(/\/+$/, ''),
    companyId: auth.companyId || null,
  }
}

async function setWhatsAppPending(payload) {
  const text = String(payload?.message || '').trim()
  const prospectId = Number(payload?.prospectId || 0) || null
  const phoneDigits = String(payload?.phoneDigits || '').replace(/\D/g, '')
  if (!prospectId || !text) return { ok: false, error: 'payload_incompleto' }
  await chrome.storage.local.set({
    [WA_PENDING_KEY]: {
      message: text,
      messagePrefix: text.slice(0, 240),
      prospectId,
      phoneDigits,
      prospectName: String(payload?.prospectName || '').trim(),
      sendUrl: String(payload?.sendUrl || '').trim(),
      createdAt: Date.now(),
    },
  })
  return { ok: true }
}

async function clearProspectWatch(prospectId) {
  const pid = Number(prospectId || 0)
  if (!pid) return { ok: false }
  const stored = await chrome.storage.local.get(WA_PENDING_KEY)
  const pending = stored?.[WA_PENDING_KEY]
  if (pending && Number(pending.prospectId) === pid) {
    await chrome.storage.local.remove(WA_PENDING_KEY)
  }
  return { ok: true }
}

async function notifyNexusTabs(message) {
  try {
    const tabs = await chrome.tabs.query({ url: NEXUS_ORIGINS })
    for (const tab of tabs || []) {
      if (!tab?.id) continue
      chrome.tabs.sendMessage(tab.id, message).catch(() => {})
    }
  } catch {
    /* ignore */
  }
}

async function waitForTabReady(tabId, timeoutMs) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
      const tab = await chrome.tabs.get(tabId)
      if (tab?.status === 'complete') return true
    } catch {
      return false
    }
    await sleep(200)
  }
  return false
}

async function waitForWhatsAppComposer(tabId, timeoutMs) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    const st = await chrome.tabs.sendMessage(tabId, { type: 'NEXUS_WA_COMPOSER_READY' }).catch(() => null)
    if (st && st.loggedIn === false) return false
    if (st?.ready) return true
    await sleep(250)
  }
  return null
}

async function pasteWhatsAppMainWorldOnce(tabId, text) {
  const want = String(text || '').trim()
  if (!want || !tabId) return false
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: (msg) => {
        const el =
          document.querySelector('#main footer [contenteditable="true"][data-lexical-editor="true"]') ||
          document.querySelector('#main footer [contenteditable="true"][role="textbox"]') ||
          document.querySelector('footer [contenteditable="true"][role="textbox"]') ||
          document.querySelector('footer div[contenteditable="true"]')
        if (!el) return false
        const norm = (s) =>
          String(s || '')
            .replace(/\s+/g, ' ')
            .trim()
            .toLowerCase()
        const wantN = norm(msg)
        const looksComplete = (raw) => {
          const c = norm(raw)
          if (!c || !wantN) return false
          if (c === wantN) return true
          if (c.includes(wantN) && c.length <= wantN.length + 12) return true
          const minLen = Math.max(24, Math.floor(wantN.length * 0.92))
          return (
            c.length >= minLen &&
            Math.abs(c.length - wantN.length) <= 12 &&
            c.includes(wantN.slice(0, Math.min(40, wantN.length)))
          )
        }
        el.focus()
        try {
          document.execCommand('selectAll', false, undefined)
          document.execCommand('delete', false, undefined)
        } catch {
          /* ignore */
        }
        try {
          const dt = new DataTransfer()
          dt.setData('text/plain', msg)
          dt.setData('text', msg)
          el.dispatchEvent(
            new ClipboardEvent('paste', {
              clipboardData: dt,
              bubbles: true,
              cancelable: true,
            }),
          )
        } catch {
          /* try insertText below */
        }
        if (looksComplete(el.innerText || el.textContent || '')) return true
        try {
          document.execCommand('selectAll', false, undefined)
          document.execCommand('delete', false, undefined)
          document.execCommand('insertText', false, msg)
        } catch {
          return false
        }
        return looksComplete(el.innerText || el.textContent || '')
      },
      args: [want],
    })
    return Boolean(injected?.[0]?.result)
  } catch {
    return false
  }
}

function stripWhatsAppTextParam(rawUrl) {
  const s = String(rawUrl || '').trim()
  if (!s) return ''
  try {
    const u = new URL(s)
    u.searchParams.delete('text')
    return u.toString()
  } catch {
    return s.split(/[?&]text=/)[0] || s
  }
}

async function armOpenWhatsApp({ sendUrl, prospectId, message, phoneDigits, prospectName }) {
  // Borrador completo: no truncar (antes slice(0,500) cortaba el mensaje).
  const text = String(message || '').trim()
  const pid = prospectId ? Number(prospectId) : null
  const digits = String(phoneDigits || '').replace(/\D/g, '')
  // Nunca ?text=: WhatsApp Web trunca el query. Pegamos el texto completo en MAIN.
  let url = digits
    ? `https://web.whatsapp.com/send?phone=${digits}`
    : stripWhatsAppTextParam(String(sendUrl || '').trim())
  if (!url || !pid) return { ok: false, error: 'Falta URL o prospecto WhatsApp' }

  if (text) {
    await setWhatsAppPending({
      sendUrl: url,
      message: text,
      prospectId: pid,
      phoneDigits: digits,
      prospectName,
    })
  }

  let tabs = []
  try {
    tabs = await chrome.tabs.query({ url: ['https://web.whatsapp.com/*'] })
  } catch {
    tabs = []
  }
  let tab = (tabs || []).find((t) => t.id) || null
  const alreadyOnWa = Boolean(tab?.id)
  if (tab?.id) await chrome.tabs.update(tab.id, { active: true, url })
  else tab = await chrome.tabs.create({ url, active: true })

  if (!tab?.id || !text) return { ok: true, tabId: tab?.id, mode: 'whatsapp-web' }

  await waitForTabReady(tab.id, alreadyOnWa ? 4000 : 12000).catch(() => {})
  const ready = await waitForWhatsAppComposer(tab.id, alreadyOnWa ? 8000 : 15000)
  if (ready === false) {
    return { ok: true, tabId: tab.id, mode: 'whatsapp-web', pasted: false, needsLogin: true }
  }

  await sleep(400)
  const filled = await pasteWhatsAppMainWorldOnce(tab.id, text)
  return { ok: true, tabId: tab.id, mode: 'whatsapp-web', pasted: Boolean(filled) }
}

async function handleWhatsAppOutboundSent({ prospectId }) {
  const auth = await getAuth()
  if (!auth) return { ok: false, error: 'Nexus no está autenticado. Abrí Nexus y logueate.' }
  const pid = prospectId ? Number(prospectId) : null
  if (!pid) return { ok: false, error: 'Falta prospectId' }

  const res = await fetch(`${auth.apiBaseUrl}/prospects/${pid}/whatsapp-assisted/mark-sent`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${auth.token}` },
  })
  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    return { ok: false, error: `API ${res.status}: ${errText.slice(0, 200)}` }
  }
  await chrome.storage.local.remove(WA_PENDING_KEY)
  await notifyNexusTabs({ type: 'NEXUS_WHATSAPP_SENT_REGISTERED', prospectId: pid })
  return { ok: true, prospectId: pid }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const type = message?.type
  if (type === 'NEXUS_SYNC_AUTH') {
    void syncAuth(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }
  if (type === 'NEXUS_ARM_WHATSAPP_CHAT') {
    void armOpenWhatsApp(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }
  if (type === 'NEXUS_SET_WHATSAPP_PENDING') {
    void setWhatsAppPending(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }
  if (type === 'NEXUS_CLEAR_PROSPECT_WATCH') {
    void clearProspectWatch(message?.prospectId)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }
  if (type === 'NEXUS_WHATSAPP_OUTBOUND_SENT') {
    void handleWhatsAppOutboundSent(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }
  return false
})
