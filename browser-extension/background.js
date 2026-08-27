const ASSIST_KEY = 'nexusLinkedInAssist'

const OPEN_CHAT_KEY = 'nexusLinkedInOpenChat'

const UTILS_FILE = 'linkedin-assist-utils.js'

const INBOUND_UTILS_FILE = 'linkedin-inbound-utils.js'

const AUTH_KEY = 'nexusAuth'

const WATCH_KEY = 'nexusLiWatch'

const PENDING_KEY = 'nexusLiPendingSend'
const WA_PENDING_KEY = 'nexusWaPendingSend'
const WA_WATCH_LIST_KEY = 'nexusWaWatchList'
const WA_WATCH_MAX = 40
const WA_WATCH_TTL_MS = 48 * 60 * 60 * 1000

const DEFAULT_API = 'http://127.0.0.1:8002'

/** LI-SAFE: no probes, no Voyager degree, no auto-open/paste en perfiles. */
const LI_SAFE_NO_PROFILE_PROBE = true

/** LI-IN: mínimo entre polls de Messaging (ms). Con watch, más seguido. */
const LI_INBOUND_MIN_GAP_MS = 60_000
const LI_INBOUND_WATCH_GAP_MS = 12_000
let lastLiInboundPollAt = 0

const chatOpenInflight = new Set()

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = String(changeInfo.url || tab?.url || '')
  if (!url.includes('linkedin.com/in/')) return
  if (changeInfo.status !== 'complete' && changeInfo.status !== 'loading') return
  void maybeAutoOpenChat(tabId, url)
})

async function maybeAutoOpenChat(tabId, url) {
  if (LI_SAFE_NO_PROFILE_PROBE) return
  const stored = await chrome.storage.session.get(OPEN_CHAT_KEY)
  const job = stored?.[OPEN_CHAT_KEY]
  if (!job?.profileUrl) return
  const slug = slugFromProfileUrl(url)
  if (!slugMatchesJob(slug, job)) return
  if (chatOpenInflight.has(tabId)) return
  chatOpenInflight.add(tabId)
  try {
    const result = await openChatOnTab(tabId)
    const text = String(job.message || '').trim()
    if (result?.ok && text) {
      await pasteMessageOnTab(result.tabId || tabId, text)
    }
  } finally {
    chatOpenInflight.delete(tabId)
  }
}

function slugMatchesJob(slug, job) {
  const want = String(job.profileSlug || slugFromProfileUrl(job.profileUrl) || '').toLowerCase()
  const cur = String(slug || '').toLowerCase()
  if (!want) return true
  if (!cur) return true
  if (cur === want) return true
  const strip = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  if (strip(cur) === strip(want)) return true
  const lt = want.split('-').pop()
  const rt = cur.split('-').pop()
  return Boolean(lt && rt && lt.length >= 6 && lt === rt)
}

async function armOpenChat({ profileUrl, prospectId, message, preferExisting = false }) {
  const url = normalizeProfileUrl(profileUrl)
  if (!url) return { ok: false, error: 'URL de perfil inválida' }
  const slug = slugFromProfileUrl(url)
  const text = String(message || '').trim()
  const pid = prospectId ? Number(prospectId) : null

  await chrome.storage.session.set({
    [OPEN_CHAT_KEY]: {
      profileUrl: url,
      profileSlug: slug,
      prospectId: pid,
      message: text,
      startedAt: Date.now(),
    },
  })
  if (text) {
    await chrome.storage.session.set({
      [ASSIST_KEY]: {
        profileUrl: url,
        message: text,
        prospectId: pid,
        startedAt: Date.now(),
      },
    })
    await setLinkedInPending({
      profileUrl: url,
      message: text,
      prospectId: pid,
      isReply: false,
    })
    void armLinkedInInboundWatchBurst()
  }

  const cached = await getCachedComposeUrl(slug)

  // Preferir pestaña ya abierta (perfil / messaging) — menos pestañas.
  let existing =
    (await findOpenProfileTab(url, slug)) ||
    (await waitForMessagingTab(slug, preferExisting ? 2500 : 400).catch(() => null)) ||
    null

  if (cached) {
    let tab = existing || (preferExisting ? null : await findAnyLinkedInTab())
    if (tab?.id) {
      await chrome.tabs.update(tab.id, { active: true, url: cached })
    } else {
      tab = await chrome.tabs.create({ url: cached, active: true })
    }
    await chrome.storage.session.remove(OPEN_CHAT_KEY)
    const pasted = text ? await pasteMessageOnTab(tab.id, text) : false
    return {
      ok: true,
      armed: true,
      composeUrl: cached,
      method: 'cache',
      mode: pasted ? 'extension' : 'extension-chat-open',
      pasted,
    }
  }

  const messagingTab = existing?.id
    ? null
    : await waitForMessagingTab(slug, preferExisting ? 3000 : 8000)
  if (messagingTab?.id && text) {
    await chrome.storage.session.remove(OPEN_CHAT_KEY)
    const pasted = await pasteMessageOnTab(messagingTab.id, text)
    return {
      ok: true,
      armed: true,
      mode: pasted ? 'extension' : 'extension-chat-open',
      pasted,
      tabId: messagingTab.id,
    }
  }

  // Esperar perfil → abrir chat → pegar.
  let tab = existing
  if (!tab?.id) {
    tab = await waitForProfileTab(url, slug, preferExisting ? 8000 : 25000)
  }
  if (!tab?.id && !preferExisting) {
    tab = await chrome.tabs.create({ url, active: true })
    await waitForTabReady(tab.id, 15000).catch(() => {})
  }
  if (!tab?.id) {
    return { ok: false, error: 'No se encontró la pestaña del perfil LinkedIn' }
  }
  try {
    await chrome.tabs.update(tab.id, { active: true })
  } catch {
    /* ignore */
  }
  const result = await openChatOnTab(tab.id)
  let pasted = false
  if (result?.ok && text) {
    pasted = await pasteMessageOnTab(result.tabId || tab.id, text)
  }
  return {
    ok: Boolean(result?.ok),
    armed: true,
    ...result,
    mode: pasted ? 'extension' : result?.mode || 'extension-chat-open',
    pasted,
  }
}

/** Pega el borrador UNA vez (mismo criterio que WA: MAIN world, sin storm de reintentos). */
async function pasteMessageOnTab(tabId, message) {
  const text = String(message || '').trim()
  if (!tabId || !text) return false

  notifyAssistStatus(tabId, 'Pegando mensaje…', 'info')
  await waitForTabReady(tabId, 12000).catch(() => {})

  // Esperar composer (MAIN), no spamear paste.
  let composerReady = false
  for (let i = 0; i < 20; i += 1) {
    const has = await readLinkedInComposerExists(tabId)
    if (has) {
      composerReady = true
      break
    }
    await sleep(350)
  }
  if (!composerReady) {
    notifyAssistStatus(tabId, 'Chat abierto. Pegá con Ctrl+V si hace falta.', 'warn')
    return false
  }

  await sleep(400)

  // Ya está el borrador (p.ej. overlay prefilled) → no volver a pegar.
  if (await readLinkedInComposerMatch(tabId, text)) {
    await markLinkedInPasteDone(text)
    notifyAssistStatus(tabId, 'Mensaje listo — enviá con Enter.', 'success')
    await chrome.storage.session.remove(ASSIST_KEY)
    return true
  }

  // Claim: una sola escritura MAIN.
  try {
    const stored = await chrome.storage.local.get(PENDING_KEY)
    const pending = stored?.[PENDING_KEY]
    if (pending?.pasteInserted) {
      const ok = await readLinkedInComposerMatch(tabId, text)
      if (ok) notifyAssistStatus(tabId, 'Mensaje listo — enviá con Enter.', 'success')
      return ok
    }
    if (pending) {
      pending.pasteInserted = true
      await chrome.storage.local.set({ [PENDING_KEY]: pending })
    }
  } catch {
    /* ignore */
  }

  const pasted = await pasteLinkedInMainWorldOnce(tabId, text)
  if (pasted) {
    await markLinkedInPasteDone(text)
    await chrome.storage.session.remove(ASSIST_KEY)
    notifyAssistStatus(tabId, 'Mensaje pegado — enviá con Enter.', 'success')
    return true
  }

  notifyAssistStatus(tabId, 'Chat abierto. Pegá con Ctrl+V si hace falta.', 'warn')
  return false
}

async function markLinkedInPasteDone(text) {
  try {
    const stored = await chrome.storage.local.get(PENDING_KEY)
    const pending = stored?.[PENDING_KEY]
    if (!pending) return
    pending.pastedAt = Date.now()
    pending.pasteInserted = true
    pending.pasteDone = true
    pending.messagePrefix = String(text || '').slice(0, 240)
    await chrome.storage.local.set({ [PENDING_KEY]: pending })
  } catch {
    /* ignore */
  }
}

async function readLinkedInComposerExists(tabId) {
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: () => {
        const el =
          document.querySelector('div.msg-form__contenteditable[contenteditable="true"]') ||
          document.querySelector('.msg-form__msg-content-container div[contenteditable="true"]') ||
          document.querySelector('form.msg-form div[contenteditable="true"]') ||
          document.querySelector('div.msg-overlay-conversation-bubble div[contenteditable="true"]') ||
          document.querySelector('div[role="textbox"][contenteditable="true"]')
        return Boolean(el)
      },
    })
    return Boolean(injected?.[0]?.result)
  } catch {
    return false
  }
}

async function readLinkedInComposerMatch(tabId, text) {
  const want = String(text || '').trim()
  if (!want || !tabId) return false
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: (msg) => {
        const el =
          document.querySelector('div.msg-form__contenteditable[contenteditable="true"]') ||
          document.querySelector('.msg-form__msg-content-container div[contenteditable="true"]') ||
          document.querySelector('form.msg-form div[contenteditable="true"]') ||
          document.querySelector('div.msg-overlay-conversation-bubble div[contenteditable="true"]') ||
          document.querySelector('div[role="textbox"][contenteditable="true"]')
        if (!el) return false
        const cur = String(el.innerText || el.textContent || '')
          .replace(/\s+/g, ' ')
          .trim()
          .toLowerCase()
        const w = String(msg || '')
          .replace(/\s+/g, ' ')
          .trim()
          .toLowerCase()
        if (!cur || !w) return false
        if (cur === w) return true
        if (w.length >= 12 && cur.includes(w)) return true
        if (w.length >= 24 && cur.includes(w.slice(0, 24))) return true
        return false
      },
      args: [want],
    })
    return Boolean(injected?.[0]?.result)
  } catch {
    return false
  }
}

/** Una sola escritura MAIN (ClipboardEvent), igual que WhatsApp. */
async function pasteLinkedInMainWorldOnce(tabId, text) {
  const want = String(text || '').trim()
  if (!want || !tabId) return false
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: (msg) => {
        const el =
          document.querySelector('div.msg-form__contenteditable[contenteditable="true"]') ||
          document.querySelector('.msg-form__msg-content-container div[contenteditable="true"]') ||
          document.querySelector('form.msg-form div[contenteditable="true"]') ||
          document.querySelector('div.msg-overlay-conversation-bubble div[contenteditable="true"]') ||
          document.querySelector('div[role="textbox"][contenteditable="true"]')
        if (!el) return false
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
          return false
        }
        const cur = String(el.innerText || el.textContent || '')
          .replace(/\s+/g, ' ')
          .trim()
          .toLowerCase()
        const w = String(msg || '')
          .replace(/\s+/g, ' ')
          .trim()
          .toLowerCase()
        return Boolean(cur && w && (cur === w || cur.includes(w.slice(0, Math.min(40, w.length)))))
      },
      args: [want],
    })
    return Boolean(injected?.[0]?.result)
  } catch {
    return false
  }
}

async function waitForMessagingTab(slug, timeoutMs = 10000) {
  const start = Date.now()
  const want = String(slug || '').toLowerCase()
  while (Date.now() - start < timeoutMs) {
    try {
      const tabs = await chrome.tabs.query({
        url: ['*://www.linkedin.com/messaging/*', '*://linkedin.com/messaging/*'],
      })
      if (tabs.length) {
        // Preferir pestaña activa / más nueva.
        tabs.sort((a, b) => (b.id || 0) - (a.id || 0))
        const active = tabs.find((t) => t.active) || tabs[0]
        if (active?.id) return active
      }
      // Compose a veces queda como /in/ con overlay; también mirar perfiles del slug.
      if (want) {
        const profile = await findOpenProfileTab(`https://www.linkedin.com/in/${want}/`, want)
        if (profile?.id) {
          const path = await getTabPath(profile.id)
          if (path.includes('/messaging')) return profile
        }
      }
    } catch {
      /* ignore */
    }
    await sleep(300)
  }
  return null
}

/**
 * En la pestaña del perfil: leer href de Mensaje o hacer click en el botón.
 */
async function openChatOnTab(tabId) {
  notifyAssistStatus(tabId, 'Buscando botón Mensaje…', 'info')
  await waitForTabReady(tabId, 15000).catch(() => {})

  const job = (await chrome.storage.session.get(OPEN_CHAT_KEY))?.[OPEN_CHAT_KEY]
  const slug =
    slugFromProfileUrl((await chrome.tabs.get(tabId).catch(() => null))?.url || '') ||
    job?.profileSlug ||
    null

  for (let round = 0; round < 50; round += 1) {
    const path = await getTabPath(tabId)
    if (path.includes('/messaging')) {
      await chrome.storage.session.remove(OPEN_CHAT_KEY)
      notifyAssistStatus(tabId, 'Chat abierto ✓', 'success')
      return { ok: true, tabId, mode: 'extension-chat-open' }
    }

    // 1) Leer href del botón Mensaje (sin Voyager).
    const found = await injectFindMessageCompose(tabId)
    if (found?.composeUrl) {
      if (slug) await setCachedComposeUrl(slug, found.composeUrl)
      if (job?.prospectId || slug) {
        void handleLearnedProfileUrn({
          profileSlug: slug,
          prospectId: job?.prospectId,
          urn: found.urn || null,
          composeUrl: found.composeUrl,
        })
      }
      notifyAssistStatus(tabId, 'Abriendo chat…', 'info')
      await chrome.tabs.update(tabId, { url: found.composeUrl })
      await sleep(1000)
      continue
    }

    // 2) Click real en Mensaje (MAIN world — React).
    if (round === 2 || round === 8 || round === 16) {
      notifyAssistStatus(tabId, 'Tocando Mensaje…', 'info')
      await injectClickMessageButton(tabId)
      await sleep(900)
      continue
    }

    // 3) Content script backup.
    if (round % 4 === 3) {
      await chrome.tabs.sendMessage(tabId, { type: 'NEXUS_OPEN_CHAT_NOW' }).catch(() => null)
    }

    await sleep(400)
  }

  return {
    ok: false,
    tabId,
    mode: 'extension-profile-only',
    warning: 'No encontré el botón Mensaje. ¿Sos conexión de 1er grado?',
  }
}

/** Busca a[href*="/messaging/compose"] en el DOM del perfil. */
async function injectFindMessageCompose(tabId) {
  try {
    const injection = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'ISOLATED',
      func: () => {
        function buildCompose(urn) {
          const id = String(urn || '').trim()
          if (!id) return null
          const enc = encodeURIComponent(`urn:li:fsd_profile:${id}`)
          return (
            `https://www.linkedin.com/messaging/compose/` +
            `?profileUrn=${enc}&recipient=${encodeURIComponent(id)}` +
            `&screenContext=NON_SELF_PROFILE_VIEW&interop=msgOverlay`
          )
        }
        function urnFromHref(href) {
          try {
            const u = new URL(href, location.origin)
            const r = u.searchParams.get('recipient')
            if (r && /^[A-Za-z0-9_-]{10,}$/.test(r)) return r
            const p = decodeURIComponent(u.searchParams.get('profileUrn') || '')
            const m = p.match(/fsd_profile:([A-Za-z0-9_-]+)/i)
            return m ? m[1] : null
          } catch {
            return null
          }
        }
        function normalize(href) {
          if (!href) return null
          try {
            const u = new URL(href, location.origin)
            if (!u.pathname.includes('/messaging/compose') && !u.searchParams.has('profileUrn')) {
              return null
            }
            const urn = urnFromHref(u.toString())
            if (urn) return { composeUrl: buildCompose(urn), urn, method: 'dom-href' }
            u.searchParams.set('interop', 'msgOverlay')
            u.searchParams.set('screenContext', 'NON_SELF_PROFILE_VIEW')
            u.searchParams.delete('lipi')
            return { composeUrl: u.toString(), urn: null, method: 'dom-href-raw' }
          } catch {
            return null
          }
        }

        const selectors = [
          'a[href*="/messaging/compose"]',
          'a[href*="profileUrn="]',
          '.pvs-profile-actions a[href*="messaging"]',
          'main a[href*="/messaging/compose"]',
        ]
        for (const sel of selectors) {
          for (const el of document.querySelectorAll(sel)) {
            const href = el.getAttribute('href') || el.href
            const hit = normalize(href)
            if (hit) return hit
          }
        }

        // HTML crudo (CTA a veces no “visible” aún).
        try {
          const html = document.documentElement?.innerHTML || ''
          const m = html.match(/href="(\/messaging\/compose\/?[^"]+)"/i)
          if (m) {
            const hit = normalize(m[1])
            if (hit) return hit
          }
        } catch {
          /* ignore */
        }
        return null
      },
    })
    return injection?.[0]?.result || null
  } catch (err) {
    console.warn('[Nexus LI] injectFindMessageCompose', err)
    return null
  }
}

/** Click en botón/anchor Mensaje (MAIN + ISOLATED). */
async function injectClickMessageButton(tabId) {
  const clicker = () => {
    const isMsg = (el) => {
      const label = `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
        .toLowerCase()
        .replace(/\s+/g, ' ')
      if (!(label.includes('mensaje') || label.includes('message'))) return false
      if (label.includes('inmail') || label.includes('messaging')) return false
      return true
    }
    const roots = [
      ...document.querySelectorAll(
        '.pvs-profile-actions, .pv-top-card-v2-ctas, .ph5.pb5, main .pv-top-card, section.artdeco-card .ph5',
      ),
      document,
    ]
    for (const root of roots) {
      const link = root.querySelector?.('a[href*="/messaging/compose"]')
      if (link) {
        link.click()
        return { clicked: true, via: 'compose-link' }
      }
      for (const el of root.querySelectorAll?.('a, button, div[role="button"]') || []) {
        if (!isMsg(el)) continue
        el.click()
        return { clicked: true, via: 'label' }
      }
    }
    return { clicked: false }
  }

  for (const world of ['MAIN', 'ISOLATED']) {
    try {
      const injection = await chrome.scripting.executeScript({
        target: { tabId },
        world,
        func: clicker,
      })
      if (injection?.[0]?.result?.clicked) return injection[0].result
    } catch {
      /* try next world */
    }
  }
  return { clicked: false }
}

const COMPOSE_CACHE_KEY = 'nexusLiComposeCache'

async function getCachedComposeUrl(slug) {
  try {
    const stored = await chrome.storage.local.get(COMPOSE_CACHE_KEY)
    const entry = stored?.[COMPOSE_CACHE_KEY]?.[String(slug).toLowerCase()]
    if (!entry?.composeUrl) return null
    if (Date.now() - Number(entry.at || 0) > 30 * 24 * 60 * 60 * 1000) return null
    return entry.composeUrl
  } catch {
    return null
  }
}

async function setCachedComposeUrl(slug, composeUrl) {
  if (!slug || !composeUrl) return
  try {
    const stored = await chrome.storage.local.get(COMPOSE_CACHE_KEY)
    const cache = stored?.[COMPOSE_CACHE_KEY] || {}
    cache[String(slug).toLowerCase()] = { composeUrl, at: Date.now() }
    await chrome.storage.local.set({ [COMPOSE_CACHE_KEY]: cache })
  } catch {
    /* ignore */
  }
}

async function findAnyLinkedInTab() {
  try {
    const tabs = await chrome.tabs.query({
      url: ['*://www.linkedin.com/*', '*://linkedin.com/*'],
    })
    for (const t of tabs || []) {
      if (!t?.id) continue
      if (nexusOwnedProbeTabs.has(t.id)) continue
      if (/nexus_probe=/i.test(String(t.url || ''))) continue
      return t
    }
    return null
  } catch {
    return null
  }
}

async function resolveComposeUrlForProfile(profileUrl) {
  const url = normalizeProfileUrl(profileUrl)
  const slug = slugFromProfileUrl(url)
  if (!slug) return { composeUrl: null, method: 'no-slug' }

  const cached = await getCachedComposeUrl(slug)
  if (cached) return { composeUrl: cached, method: 'cache' }

  // Si el perfil ya está abierto, leer el botón Mensaje ahí.
  const tab = (await findOpenProfileTab(url, slug)) || (await findNewestProfileTab(slug))
  if (tab?.id) {
    const found = await injectFindMessageCompose(tab.id)
    if (found?.composeUrl) {
      await setCachedComposeUrl(slug, found.composeUrl)
      return { composeUrl: found.composeUrl, method: found.method || 'dom', urn: found.urn }
    }
  }
  return { composeUrl: null, method: 'unresolved' }
}



chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

  const type = message?.type

  if (type === 'NEXUS_OPEN_EXTENSIONS_PAGE') {
    const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : ''
    const url = /Edg\//.test(ua) ? 'edge://extensions' : 'chrome://extensions'
    void chrome.tabs.create({ url, active: true })
    sendResponse({ ok: true })
    return false
  }

  if (type === 'NEXUS_OPEN_LINKEDIN') {

    void openLinkedInAssist(message)

      .then((result) => sendResponse(result))

      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))

    return true

  }

  if (type === 'NEXUS_INJECT_WA_NOTIFY_HOOK') {
    const tabId = sender?.tab?.id
    if (!tabId) {
      sendResponse({ ok: false })
      return false
    }
    void injectWaNotificationHook(tabId)
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }))
    return true
  }

  if (type === 'NEXUS_ARM_OPEN_CHAT') {
    void armOpenChat(message)
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

  if (type === 'NEXUS_RESOLVE_COMPOSE_URL') {
    void resolveComposeUrlForProfile(message?.profileUrl)
      .then((result) => sendResponse({ ok: Boolean(result?.composeUrl), ...result }))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  if (type === 'NEXUS_FINISH_ASSIST_ON_TAB') {
    const tabId = sender?.tab?.id
    const text = String(message?.message || '').trim()
    if (!tabId || !text) {
      sendResponse({ ok: false, error: 'tab_or_message_missing' })
      return false
    }
    void finishLinkedInAssistOnTab(tabId, text)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  if (type === 'NEXUS_ASSIST_PASTE') {
    const tabId = sender?.tab?.id
    const text = String(message?.message || '').trim()
    if (!tabId || !text) {
      sendResponse({ ok: false, pasted: false })
      return false
    }
    void pasteMessageOnTab(tabId, text)
      .then((pasted) => sendResponse({ ok: true, pasted: Boolean(pasted) }))
      .catch(() => sendResponse({ ok: false, pasted: false }))
    return true
  }

  if (type === 'NEXUS_ASSIST_DONE') {
    sendResponse({ ok: true })
    return false
  }

  if (type === 'NEXUS_SYNC_AUTH') {

    void syncAuth(message).then(() => {
      sendResponse({ ok: true })
    })

    return true

  }

  if (type === 'NEXUS_LINKEDIN_INBOUND_DETECTED') {

    void handleInboundDetected(message)

      .then((result) => sendResponse(result))

      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))

    return true

  }

  if (type === 'NEXUS_WHATSAPP_INBOUND_DETECTED') {
    void handleWhatsAppInboundDetected(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  if (type === 'NEXUS_WA_OPEN_CHAT_QUIET') {
    void openWhatsAppChatQuiet(message?.phoneDigits)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  if (type === 'NEXUS_WA_IS_TAB_FOCUSED') {
    const tabId = sender?.tab?.id
    if (!tabId) {
      sendResponse({ focused: true })
      return false
    }
    void chrome.tabs
      .get(tabId)
      .then(async (tab) => {
        let winFocused = false
        try {
          if (tab.windowId != null) {
            const win = await chrome.windows.get(tab.windowId)
            winFocused = Boolean(win?.focused)
          }
        } catch {
          winFocused = false
        }
        sendResponse({
          focused: Boolean(tab.active && winFocused),
          active: Boolean(tab.active),
          winFocused,
        })
      })
      .catch(() => sendResponse({ focused: true }))
    return true
  }

  if (type === 'NEXUS_WA_STORE_READ') {
    const tabId = sender?.tab?.id
    void readWhatsAppStoreWatched(tabId, message?.watchList || [])
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err), rows: [] }))
    return true
  }

  if (type === 'NEXUS_WA_TELEMETRY') {
    void postWaExtTelemetry({
      store_ok: message?.store_ok,
      store_source: message?.store_source,
      store_error: message?.store_error,
      chats: message?.chats,
      matched: message?.matched,
      inbound: message?.inbound,
      candidates: message?.candidates,
      reported: message?.reported,
      reason: message?.reason,
    }).then(() => sendResponse({ ok: true }))
    return true
  }

  if (type === 'NEXUS_LINKEDIN_OUTBOUND_SENT') {

    void handleOutboundSent(message)

      .then((result) => sendResponse(result))

      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))

    return true

  }

  // LI-SAFE: armar vigilancia inbound SIN mark-sent (el frontend ya marcó / va a marcar).
  if (type === 'NEXUS_LI_ARM_INBOUND_WATCH') {
    void handleArmInboundWatch(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  // LI-IN: poll forzado desde Nexus (Diagnóstico + Detectar).
  if (type === 'NEXUS_LI_POLL_INBOUND_NOW') {
    void handlePollInboundNow(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  if (type === 'NEXUS_LINKEDIN_CONNECTION_STATUS') {
    const tabId = Number(sender?.tab?.id || 0)
    void (async () => {
      let prospectId = message?.prospectId ? Number(message.prospectId) : null
      if (!prospectId) {
        prospectId = await lookupProbeProspectId({
          tabId,
          slug: message?.profileSlug || '',
        })
      }
      return handleConnectionStatus({
        profileSlug: message?.profileSlug,
        status: message?.status,
        prospectId,
      })
    })()
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))

    return true
  }

  if (type === 'NEXUS_LOOKUP_PROBE_PROSPECT') {
    const tabId = Number(sender?.tab?.id || message?.tabId || 0)
    void lookupProbeProspectId({
      tabId,
      slug: message?.profileSlug || '',
    })
      .then((pid) => sendResponse({ ok: Boolean(pid), prospectId: pid || null }))
      .catch(() => sendResponse({ ok: false, prospectId: null }))
    return true
  }

  if (type === 'NEXUS_LEARNED_PROFILE_URN') {
    void handleLearnedProfileUrn(message)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  if (type === 'NEXUS_SET_LINKEDIN_PENDING') {

    void setLinkedInPending(message)

      .then((result) => sendResponse(result))

      .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))

    return true

  }

  if (type === 'NEXUS_PROBE_LINKEDIN_CONNECTION') {
    if (LI_SAFE_NO_PROFILE_PROBE) {
      sendResponse({
        ok: true,
        skipped: true,
        reason: 'li_safe_no_probe',
        readOk: false,
        prospectId: Number(message?.prospectId || 0) || null,
      })
      return false
    }
    // Sin withProbeLock: si otro probe está trabado, no esperar 20s en cola.
    // FORCE que reporta al SW mientras el SW espera → deadlock; usamos READ + report desde SW.
    void (async () => {
      const pid = Number(message?.prospectId || 0)
      const prospectName = message?.prospectName || message?.name || ''
      try {
        if (pid) await clearProbeResolved(pid)
        try {
          const stored = await chrome.storage.local.get([PROBE_ATTEMPT_KEY, PROBE_MAX_ATTEMPTS_KEY])
          const map = { ...(stored?.[PROBE_ATTEMPT_KEY] || {}) }
          const counts = { ...(stored?.[PROBE_MAX_ATTEMPTS_KEY] || {}) }
          delete map[String(pid)]
          delete counts[String(pid)]
          await chrome.storage.local.set({
            [PROBE_ATTEMPT_KEY]: map,
            [PROBE_MAX_ATTEMPTS_KEY]: counts,
          })
        } catch {
          /* ignore */
        }
        const result = await Promise.race([
          probeOneConnection(
            {
              linkedin_url: message?.profileUrl,
              prospect_id: message?.prospectId,
              prospect_name: prospectName,
              connection_status: message?.connectionStatus || message?.mode || 'checking',
            },
            { allowCreateTab: true, skipCooldown: true },
          ),
          sleep(32_000).then(() => ({
            ok: false,
            readOk: false,
            error: 'probe_sw_timeout',
            prospectId: pid,
            prospectName,
            via: 'voyager_badge',
          })),
        ])
        await saveLastProbeDiag({ ...result, prospectId: pid || result?.prospectId, prospectName })
        sendResponse({
          ok: Boolean(result?.ok),
          connectionStatus: result?.connectionStatus || result?.verdict || null,
          ...(result && typeof result === 'object' ? result : {}),
        })
      } catch (err) {
        const fail = {
          ok: false,
          readOk: false,
          error: String(err?.message || err),
          prospectId: pid,
          prospectName,
        }
        try {
          await saveLastProbeDiag(fail)
        } catch {
          /* ignore */
        }
        try {
          sendResponse(fail)
        } catch {
          /* ignore */
        }
      }
    })()
    return true
  }

  if (type === 'NEXUS_PROBE_PENDING_CONNECTIONS_NOW') {
    if (LI_SAFE_NO_PROFILE_PROBE) {
      sendResponse({ ok: true, skipped: true, reason: 'li_safe_no_probe', results: [] })
      return false
    }
    // force:true limpia cooldown (debug manual). Auto-probe NO debe resetear.
    void (async () => {
      try {
        if (message?.force) {
          await chrome.storage.local.remove([PROBE_ATTEMPT_KEY, PROBE_MAX_ATTEMPTS_KEY])
        }
      } catch {
        /* ignore */
      }
      const result = await probePendingLinkedInConnections()
      sendResponse({ ok: Boolean(result?.ok !== false), ...(result || {}) })
    })().catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }))
    return true
  }

  if (type === 'NEXUS_GET_LINKEDIN_CSRF') {
    void getLinkedInCsrfToken()
      .then((csrf) => sendResponse({ ok: Boolean(csrf), csrf: csrf || '' }))
      .catch(() => sendResponse({ ok: false, csrf: '' }))
    return true
  }

  if (type === 'NEXUS_INJECT_OUTBOUND_UTILS') {
    const tabId = sender?.tab?.id

    if (!tabId) {

      sendResponse({ ok: false })

      return false

    }

    void chrome.scripting

      .executeScript({ target: { tabId }, files: ['linkedin-outbound-utils.js'] })

      .then(() => sendResponse({ ok: true }))

      .catch(() => sendResponse({ ok: false }))

    return true

  }

  if (type === 'NEXUS_INJECT_INBOUND_UTILS') {

    const tabId = sender?.tab?.id

    if (!tabId) {

      sendResponse({ ok: false })

      return false

    }

    void chrome.scripting

      .executeScript({ target: { tabId }, files: [INBOUND_UTILS_FILE] })

      .then(() => sendResponse({ ok: true }))

      .catch(() => sendResponse({ ok: false }))

    return true

  }

  return false

})







        

async function syncAuth({ token, apiBaseUrl, companyId }) {

  const auth = {

    token: String(token || '').trim(),

    apiBaseUrl: String(apiBaseUrl || DEFAULT_API).replace(/\/+$/, ''),

    companyId: companyId ? Number(companyId) : null,

    syncedAt: Date.now(),

  }

  const prev = await getAuth()
  await chrome.storage.local.set({ [AUTH_KEY]: auth })
  const changed =
    !prev ||
    prev.token !== auth.token ||
    Number(prev.companyId || 0) !== Number(auth.companyId || 0)
  // Siempre sondear si hay auth: el SW se duerme y el insert no abría pestañas.
  // probePending tiene lock + cooldown; no satura LinkedIn.
  // NO auto-probe en cada syncAuth: duplicaba pestañas con el sondeo de outreach.
  if (auth.token && auth.companyId && changed) {
    /* auth lista; outreach / alarm disparan el verify */
  } else if (changed) {
    /* auth limpia */
  }

}




async function getAuth() {

  const stored = await chrome.storage.local.get(AUTH_KEY)

  const auth = stored?.[AUTH_KEY]

  if (!auth?.token) return null

  return {

    token: auth.token,

    apiBaseUrl: (auth.apiBaseUrl || DEFAULT_API).replace(/\/+$/, ''),

    companyId: auth.companyId || null,

  }

}



async function openLinkedInAssist({ profileUrl, message, sessionId, prospectId, isReply, adoptOnly, openChatOnly }) {
  void persistAssistJob({
    url: normalizeProfileUrl(profileUrl),
    text: String(message || '').trim(),
    slug: slugFromProfileUrl(normalizeProfileUrl(profileUrl) || ''),
    sessionId,
    prospectId,
    isReply,
  })
  // adoptOnly: reutilizar pestaña existente / pegar; no abrir una 2ª vía aparte.
  // openChatOnly sin mensaje: solo abrir chat.
  return armOpenChat({
    profileUrl,
    prospectId,
    message: openChatOnly ? '' : message,
    preferExisting: Boolean(adoptOnly),
  })
}

async function getTabPath(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId)
    return new URL(tab.url || 'https://www.linkedin.com/').pathname.toLowerCase()
  } catch {
    return ''
  }
}

function notifyAssistStatus(tabId, status, tone = 'info') {
  chrome.tabs.sendMessage(tabId, { type: 'NEXUS_ASSIST_STATUS', status, tone }).catch(() => {})
}

async function waitForProfileTab(profileUrl, slug, timeoutMs = 20000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    const tab = (await findOpenProfileTab(profileUrl, slug)) || (await findNewestProfileTab(slug))
    if (tab?.id) return tab
    await sleep(250)
  }
  return (await findOpenProfileTab(profileUrl, slug)) || (await findNewestProfileTab(slug))
}

async function findNewestProfileTab(slug) {
  try {
    const tabs = await chrome.tabs.query({
      url: ['*://www.linkedin.com/*', '*://linkedin.com/*'],
    })
    const want = String(slug || '').toLowerCase()
    const candidates = tabs.filter((t) => {
      const u = String(t.url || '').toLowerCase()
      if (!u.includes('/in/')) return false
      if (!want) return true
      if (u.includes(want)) return true
      const idTail = want.split('-').pop()
      return Boolean(idTail && idTail.length >= 6 && u.includes(idTail))
    })
    if (!candidates.length) return null
    candidates.sort((a, b) => (b.id || 0) - (a.id || 0))
    return candidates[0]
  } catch {
    return null
  }
}

/**
 * Extrae compose URL → navega → pega. Lectura DOM en ISOLATED (fiable);
 * clicks/paste intentan MAIN y caen a ISOLATED.
 */
async function finishLinkedInAssistOnTab(tabId, text) {
  const message = String(text || '').trim()
  await waitForTabReady(tabId, 18000)
  await sleep(2200)

  let resolveMethod = null
  let composeUrl = null

  // 1) Esperar link compose o URN en el perfil (LinkedIn SPA tarda).
  for (let attempt = 0; attempt < 24 && !composeUrl; attempt += 1) {
    const extracted = await runAssistFn(tabId, 'extractAssistTarget', [], ['ISOLATED', 'MAIN'])
    if (extracted?.composeUrl) {
      composeUrl = extracted.composeUrl
      resolveMethod = extracted.method || 'extract'
      break
    }
    await sleep(450)
  }

  // 2) Click Mensaje / URN embebido.
  if (!composeUrl) {
    const resolved = await runAssistFn(tabId, 'resolveChatOpen', [12000], ['MAIN', 'ISOLATED'])
    resolveMethod = resolved?.method || resolveMethod
    if (resolved?.composeUrl) composeUrl = resolved.composeUrl
    if (
      !resolved?.composeUrl &&
      (resolved?.method === 'already-open' ||
        resolved?.method === 'overlay' ||
        resolved?.method === 'overlay-menu')
    ) {
      composeUrl = null
    }
  }

  if (composeUrl) {
    notifyAssistStatus(tabId, 'Abriendo chat de LinkedIn…', 'info')
    await chrome.tabs.update(tabId, { url: composeUrl })
    await waitForTabReady(tabId, 18000)
    await sleep(1800)
  }

  let chatReady = Boolean(composeUrl)
  for (let i = 0; i < 22; i += 1) {
    const box = await runAssistFn(tabId, 'findComposeBox', [], ['ISOLATED', 'MAIN'])
    if (box) {
      chatReady = true
      break
    }
    const open = await runAssistFn(tabId, 'isChatOpen', [], ['ISOLATED', 'MAIN'])
    if (open) {
      chatReady = true
      break
    }
    await sleep(350)
  }

  if (!chatReady) {
    return {
      ok: true,
      tabId,
      mode: 'extension-profile-only',
      resolveMethod: resolveMethod || 'failed',
      warning: 'Perfil abierto. Tocá Mensaje y pegá con Ctrl+V.',
    }
  }

  notifyAssistStatus(tabId, 'Pegando mensaje…', 'info')
  const pasted = await pasteMessageOnTab(tabId, message)
  if (pasted) {
    await chrome.storage.session.remove(ASSIST_KEY)
    return {
      ok: true,
      tabId,
      mode: 'extension',
      resolveMethod: resolveMethod || 'paste',
    }
  }

  return {
    ok: true,
    tabId,
    mode: 'extension-chat-open',
    resolveMethod: resolveMethod || 'compose',
    warning: 'Chat abierto. Si el renglón está vacío, pegá con Ctrl+V.',
  }
}

async function findOpenProfileTab(profileUrl, slug) {
  try {
    const tabs = await chrome.tabs.query({
      url: ['*://www.linkedin.com/*', '*://linkedin.com/*'],
    })
    const want = String(slug || slugFromProfileUrl(profileUrl) || '')
      .toLowerCase()
      .trim()
    if (!want) return null
    for (const t of tabs) {
      if (!t?.id) continue
      if (nexusOwnedProbeTabs.has(t.id)) continue
      if (/nexus_probe=/i.test(String(t.url || ''))) continue
      const tabSlug = slugFromProfileUrl(t.url || '')
      if (tabSlug && tabSlug.toLowerCase() === want) return t
      // Match parcial por id al final del slug (ej. 62638b70)
      const idTail = want.split('-').pop()
      if (idTail && idTail.length >= 6 && (t.url || '').toLowerCase().includes(idTail)) {
        return t
      }
    }
  } catch {
    /* ignore */
  }
  return null
}

async function persistAssistJob({ url, text, slug, sessionId, prospectId, isReply }) {
  try {
    if (slug) {
      await chrome.storage.local.set({
        [WATCH_KEY]: {
          prospectId: prospectId ? Number(prospectId) : null,
          profileSlug: slug,
          profileUrl: url,
          since: Date.now(),
        },
        [PENDING_KEY]: {
          prospectId: prospectId ? Number(prospectId) : null,
          profileSlug: slug,
          messagePrefix: String(text || '').slice(0, 240),
          messageHash: hashText(text),
          isReply: Boolean(isReply),
          since: Date.now(),
        },
      })
    }
    await chrome.storage.session.set({
      [ASSIST_KEY]: {
        profileUrl: url,
        message: text,
        sessionId: sessionId || null,
        prospectId: prospectId || null,
        startedAt: Date.now(),
      },
    })
  } catch {
    /* ignore */
  }
}

async function handleInboundDetected({ profileSlug, message, linkedinMessageId, prospectId: prospectIdHint }) {
  const auth = await getAuth()
  if (!auth) {
    return { ok: false, error: 'Nexus no está autenticado. Abrí Nexus y logueate.' }
  }

  const slug = String(profileSlug || '').trim().toLowerCase()
  if (LI_SAFE_NO_PROFILE_PROBE) {
    if (!slug || slug === 'unknown' || slug === 'watched') {
      return { ok: false, error: 'li_safe_slug_required' }
    }
  }

  let prospectId = prospectIdHint ? Number(prospectIdHint) : 0
  if (!prospectId) {
    prospectId = await resolveProspectId(auth, slug)
  } else if (LI_SAFE_NO_PROFILE_PROBE) {
    // Hint solo vale si el slug matchea al prospecto resuelto / watch.
    const resolved = await resolveProspectId(auth, slug)
    if (resolved && Number(resolved) !== Number(prospectId)) {
      return { ok: false, error: 'li_safe_prospect_slug_mismatch' }
    }
    if (!resolved && !(await watchSlugMatches(slug))) {
      return { ok: false, error: 'li_safe_unverified_hint' }
    }
  }
  if (!prospectId) {
    return { ok: false, error: 'No se encontró prospecto para este perfil LinkedIn.' }
  }

  const res = await fetch(`${auth.apiBaseUrl}/prospects/${prospectId}/linkedin-inbound`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${auth.token}`,
    },
    body: JSON.stringify({
      message: String(message || '').trim(),
      linkedin_message_id: linkedinMessageId || null,
    }),
  })

  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    return { ok: false, error: `API ${res.status}: ${errText.slice(0, 200)}` }
  }

  const data = await res.json()
  notifyNexusTabs({
    type: 'NEXUS_LINKEDIN_INBOUND_REGISTERED',
    prospectId,
    inserted: Boolean(data.inserted),
    replyDraftReady: Boolean(data.reply_draft_ready),
    replyAvailableAt: data.reply_available_at || null,
    replyDelayed: Boolean(data.reply_available_at) && Boolean(data.reply_draft_ready),
  })

  return {
    ok: true,
    inserted: Boolean(data.inserted),
    duplicate: Boolean(data.duplicate),
    echo_ignored: Boolean(data.echo_ignored),
    replyDraftReady: Boolean(data.reply_draft_ready),
    replyDelayed: Boolean(data.reply_available_at) && Boolean(data.reply_draft_ready),
  }
}

async function watchSlugMatches(profileSlug) {
  const stored = await chrome.storage.local.get([WATCH_KEY, 'nexusLiLastProfileSlug'])
  const watch = stored?.[WATCH_KEY]
  const a = normalizeLiSlugBg(profileSlug)
  const b = normalizeLiSlugBg(watch?.profileSlug || stored?.nexusLiLastProfileSlug || '')
  return Boolean(a && b && a === b)
}

async function handleWhatsAppInboundDetected({
  phoneDigits,
  message,
  whatsappMessageId,
  prospectId: prospectIdHint,
}) {
  const auth = await getAuth()
  if (!auth) {
    return { ok: false, error: 'Nexus no está autenticado. Abrí Nexus y logueate.' }
  }
  const phone = String(phoneDigits || '').replace(/\D/g, '')
  const text = String(message || '').trim()
  if (!text) {
    return { ok: false, error: 'Mensaje vacío' }
  }

  let prospectId = prospectIdHint ? Number(prospectIdHint) : 0
  let resolvedFromPhone = 0
  if (phone && phone.length >= 8) {
    const resolveRes = await fetch(
      `${auth.apiBaseUrl}/prospects/resolve-whatsapp?phone=${encodeURIComponent(phone)}`,
      { headers: { Authorization: `Bearer ${auth.token}` } },
    )
    if (resolveRes.ok) {
      const resolved = await resolveRes.json()
      resolvedFromPhone = Number(resolved.prospect_id || 0)
    }
  }

  if (prospectId && resolvedFromPhone && prospectId !== resolvedFromPhone) {
    // Preferir resolve por teléfono (fuente de verdad). Solo mismatch si ambos existen.
    // Si el sticky viene de watch list y el teléfono es basura/LID, confiar en prospectId
    // cuando resolve falló arriba — aquí ambos resolvieron distinto → abortar.
    return { ok: false, error: 'phone/prospect mismatch' }
  }
  if (!prospectId) {
    prospectId = resolvedFromPhone
  }
  // Sticky watch sin resolve: confiar en prospectId del mark-sent.
  if (!prospectId) {
    const sticky = await chrome.storage.local.get(['nexusWaLastProspectId', WA_WATCH_LIST_KEY])
    const lastPid = Number(sticky?.nexusWaLastProspectId || 0)
    if (lastPid && phone) {
      const wl = Array.isArray(sticky?.[WA_WATCH_LIST_KEY]) ? sticky[WA_WATCH_LIST_KEY] : []
      const hit = wl.find(
        (x) =>
          Number(x?.prospectId) === lastPid &&
          (!phone ||
            !x?.phone ||
            String(x.phone).replace(/\D/g, '') === phone ||
            String(x.phone).replace(/\D/g, '').slice(-10) === phone.slice(-10)),
      )
      if (hit || !phone) prospectId = lastPid
    }
  }
  if (!prospectId) {
    if (!phone || phone.length < 8) {
      return { ok: false, error: 'Teléfono o prospecto vacío' }
    }
    return { ok: false, error: `No hay prospecto para ${phone}` }
  }

  // Registrar inbound + generar borrador de réplica → cola WhatsApp assisted.
  const res = await fetch(`${auth.apiBaseUrl}/prospects/${prospectId}/whatsapp-inbound`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${auth.token}`,
    },
    body: JSON.stringify({
      message: text,
      whatsapp_message_id: whatsappMessageId || null,
      prepare_reply_draft: true,
    }),
  })
  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    return { ok: false, error: `API ${res.status}: ${errText.slice(0, 200)}` }
  }
  const data = await res.json()
  if (data?.echo_ignored) {
    return {
      ok: true,
      inserted: false,
      echoIgnored: true,
      replyDraftReady: false,
      prospectId,
    }
  }
  notifyNexusTabs({
    type: 'NEXUS_WHATSAPP_INBOUND_REGISTERED',
    prospectId,
    inserted: Boolean(data.inserted),
    replyDraftReady: Boolean(data.reply_draft_ready),
    calendarReconnectRequired: Boolean(data.calendar_reconnect_required),
    operatorMessage: data.operator_message || data.detail || null,
  })
  return {
    ok: true,
    inserted: Boolean(data.inserted),
    duplicate: Boolean(data.duplicate),
    replyDraftReady: Boolean(data.reply_draft_ready),
    calendarReconnectRequired: Boolean(data.calendar_reconnect_required),
    operatorMessage: data.operator_message || data.detail || null,
    prospectId,
  }
}



/** Ventana de vigilancia post-envío LI-SAFE (respuesta puede llegar al día siguiente). */
const LI_WATCH_MS = LI_SAFE_NO_PROFILE_PROBE ? 48 * 60 * 60 * 1000 : 2 * 60 * 60 * 1000

/**
 * Solo arma watch + poll. No llama mark-sent (LI-SAFE ya lo hace desde Nexus).
 */
async function handleArmInboundWatch({
  profileSlug,
  profileUrl,
  prospectId,
  outboundText,
  prospectName,
}) {
  let slug = normalizeLiSlugBg(profileSlug)
  if (!slug && profileUrl) {
    try {
      const path = new URL(String(profileUrl), 'https://www.linkedin.com').pathname.toLowerCase()
      const idx = path.indexOf('/in/')
      if (idx >= 0) slug = normalizeLiSlugBg(path.slice(idx + 4).split('/')[0])
    } catch {
      /* ignore */
    }
  }
  const pid = prospectId ? Number(prospectId) : 0
  if (!pid && !slug) {
    return { ok: false, error: 'Faltan prospectId o slug para vigilar respuesta.' }
  }

  const ours = String(outboundText || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 240)

  await chrome.storage.local.set({
    [WATCH_KEY]: {
      prospectId: pid || null,
      profileSlug: slug || null,
      profileUrl: profileUrl ? String(profileUrl) : null,
      prospectName: prospectName ? String(prospectName).slice(0, 120) : null,
      since: Date.now(),
    },
    nexusLiLastProspectId: pid || null,
    nexusLiLastProfileSlug: slug || null,
    nexusLiLastOutboundText: ours,
    nexusLiLastOutboundAt: Date.now(),
    nexusLiWatchUntil: Date.now() + LI_WATCH_MS,
  })

  void armLinkedInInboundWatchBurst()
  void notifyLinkedInWatchArmed({
    profileSlug: slug,
    prospectId: pid || null,
    prospectName: prospectName || null,
  })
  return { ok: true, prospectId: pid || null, profileSlug: slug || null }
}

async function handleOutboundSent({ profileSlug, prospectId }) {
  const auth = await getAuth()
  if (!auth) {
    return { ok: false, error: 'Nexus no está autenticado. Abrí Nexus y logueate.' }
  }

  let pid = prospectId ? Number(prospectId) : null
  if (!pid) {
    pid = await resolveProspectId(auth, profileSlug)
  }
  if (!pid) {
    return { ok: false, error: 'No se encontró prospecto para este perfil LinkedIn.' }
  }

  const res = await fetch(`${auth.apiBaseUrl}/prospects/${pid}/linkedin-assisted/mark-sent`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${auth.token}`,
    },
  })

  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    if (res.status === 400 && /no hay (borrador|mensaje)|pendiente/i.test(errText)) {
      await chrome.storage.local.remove(PENDING_KEY)
      notifyNexusTabs({
        type: 'NEXUS_LINKEDIN_SENT_REGISTERED',
        prospectId: pid,
      })
      await handleArmInboundWatch({ profileSlug, prospectId: pid })
      return { ok: true, already: true }
    }
    console.warn('[Nexus] mark-sent failed', res.status, errText.slice(0, 300))
    return { ok: false, error: `API ${res.status}: ${errText.slice(0, 200)}` }
  }

  await res.json().catch(() => ({}))

  let outboundText = ''
  try {
    const pendingStore = await chrome.storage.local.get(PENDING_KEY)
    outboundText = String(pendingStore?.[PENDING_KEY]?.messagePrefix || '')
      .replace(/\s+/g, ' ')
      .trim()
  } catch {
    /* ignore */
  }

  await chrome.storage.local.remove(PENDING_KEY)

  await handleArmInboundWatch({
    profileSlug,
    prospectId: pid,
    outboundText,
  })

  notifyNexusTabs({
    type: 'NEXUS_LINKEDIN_SENT_REGISTERED',
    prospectId: pid,
  })

  return { ok: true, prospectId: pid }
}



async function handleLearnedProfileUrn({ profileSlug, prospectId, urn, composeUrl }) {
  const auth = await getAuth()
  let pid = prospectId ? Number(prospectId) : null
  if (!pid && auth) {
    pid = await resolveProspectId(auth, profileSlug)
  }

  const slug = String(profileSlug || '').toLowerCase()
  if (composeUrl) {
    await setCachedComposeUrl(slug, composeUrl)
  } else if (urn) {
    const built =
      `https://www.linkedin.com/messaging/compose/` +
      `?profileUrn=${encodeURIComponent(`urn:li:fsd_profile:${urn}`)}` +
      `&recipient=${encodeURIComponent(urn)}` +
      `&screenContext=NON_SELF_PROFILE_VIEW&interop=msgOverlay`
    await setCachedComposeUrl(slug, built)
  }

  if (!auth || !pid) {
    return { ok: true, saved: false, cached: true }
  }

  try {
    const res = await fetch(`${auth.apiBaseUrl}/prospects/${pid}/linkedin-profile-urn`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${auth.token}`,
        ...(auth.companyId ? { 'X-Company-Id': String(auth.companyId) } : {}),
      },
      body: JSON.stringify({
        urn: urn || null,
        compose_url: composeUrl || null,
      }),
    })
    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status}` }
    }
    return { ok: true, saved: true, prospectId: pid }
  } catch (err) {
    return { ok: false, error: String(err?.message || err) }
  }
}

async function handleConnectionStatus({ profileSlug, status, prospectId }) {

  const auth = await getAuth()

  if (!auth) {

    return { ok: false, error: 'Nexus no está autenticado. Abrí Nexus y logueate.' }

  }

  let pid = prospectId ? Number(prospectId) : null

  if (!pid) {

    pid = await resolveProspectId(auth, profileSlug)

  }

  if (!pid) {

    return { ok: false, error: 'No se encontró prospecto para este perfil LinkedIn.' }

  }

  const res = await fetch(`${auth.apiBaseUrl}/prospects/${pid}/linkedin-connection-status`, {

    method: 'POST',

    headers: {

      'Content-Type': 'application/json',

      Authorization: `Bearer ${auth.token}`,

    },

    body: JSON.stringify({ status: String(status || 'connected') }),

  })

  if (!res.ok) {

    const errText = await res.text().catch(() => '')

    return { ok: false, error: `API ${res.status}: ${errText.slice(0, 200)}` }

  }

  const data = await res.json().catch(() => ({}))

  notifyNexusTabs({
    type: 'NEXUS_LINKEDIN_CONNECTION_REGISTERED',
    prospectId: pid,
    connectionStatus: data.connection_status || 'connected',
    messageReady: Boolean(data.message_ready),
  })

  // Cerrar pestañas sonda al reportar (await para poder volver a Nexus limpio).
  await closeAllOwnedProbeTabs()

  return {
    ok: true,
    prospectId: pid,
    connectionStatus: data.connection_status || 'connected',
    messageReady: Boolean(data.message_ready),
  }
}



async function setLinkedInPending({ profileUrl, message, prospectId, isReply }) {

  const url = normalizeProfileUrl(profileUrl)

  const slug = slugFromProfileUrl(url)

  const text = String(message || '').trim()

  if (!slug || !text) {

    return { ok: false, error: 'Faltan perfil o mensaje para vigilar el envío.' }

  }

  await chrome.storage.local.set({

    [PENDING_KEY]: {

      prospectId: prospectId ? Number(prospectId) : null,

      profileSlug: slug,

      message: text,

      messagePrefix: text.slice(0, 240),

      messageHash: hashText(text),

      isReply: Boolean(isReply),

      since: Date.now(),

      pasteInserted: false,

      pasteDone: false,

    },

    [WATCH_KEY]: {

      prospectId: prospectId ? Number(prospectId) : null,

      profileSlug: slug,

      profileUrl: url,

      since: Date.now(),

    },
    nexusLiLastProspectId: prospectId ? Number(prospectId) : null,
    nexusLiLastProfileSlug: slug,
    nexusLiWatchUntil: Date.now() + LI_WATCH_MS,

  })

  return { ok: true }

}

async function upsertWaWatchTargetBg({ prospectId, phoneDigits, prospectName, outboundText }) {
  const phone = String(phoneDigits || '').replace(/\D/g, '')
  const pid = Number(prospectId || 0) || 0
  if (!pid && phone.length < 8) return
  const stored = await chrome.storage.local.get([WA_WATCH_LIST_KEY])
  const raw = Array.isArray(stored?.[WA_WATCH_LIST_KEY]) ? stored[WA_WATCH_LIST_KEY] : []
  const now = Date.now()
  const stripAr = (d) => {
    const x = String(d || '').replace(/\D/g, '')
    if (x.startsWith('549') && x.length >= 12) return `54${x.slice(3)}`
    if (x.startsWith('54') && !x.startsWith('549') && x.length >= 11) return `549${x.slice(2)}`
    return x
  }
  const samePhone = (a, b) => {
    const da = String(a || '').replace(/\D/g, '')
    const db = String(b || '').replace(/\D/g, '')
    if (!da || !db || da.length < 8 || db.length < 8) return false
    if (da === db) return true
    if (stripAr(da) === stripAr(db)) return true
    return da.length >= 10 && db.length >= 10 && da.slice(-10) === db.slice(-10)
  }
  const next = raw
    .filter((x) => now - Number(x?.since || 0) < WA_WATCH_TTL_MS)
    .filter(
      (x) =>
        !(pid && Number(x?.prospectId) === pid) &&
        !(phone.length >= 8 && x?.phone && samePhone(x.phone, phone)),
    )
  next.unshift({
    prospectId: pid,
    phone,
    name: String(prospectName || '').trim(),
    outboundText: String(outboundText || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 240),
    since: now,
  })
  await chrome.storage.local.set({
    [WA_WATCH_LIST_KEY]: next.slice(0, WA_WATCH_MAX),
    nexusWaWatchUntil: now + 2 * 60 * 60 * 1000,
  })
}

/** Deja de vigilar WA/LI para un prospecto (pausa, handoff, eliminar). */
async function clearProspectWatch(prospectId) {
  const pid = Number(prospectId || 0) || 0
  if (!pid) return { ok: false, error: 'prospectId required' }
  const stored = await chrome.storage.local.get([
    WA_WATCH_LIST_KEY,
    WATCH_KEY,
    'nexusWaLastProspectId',
    'nexusLiLastProspectId',
    'nexusWaWatchUntil',
  ])
  const raw = Array.isArray(stored?.[WA_WATCH_LIST_KEY]) ? stored[WA_WATCH_LIST_KEY] : []
  const nextWa = raw.filter((x) => Number(x?.prospectId || 0) !== pid)
  const patch = { [WA_WATCH_LIST_KEY]: nextWa }
  if (Number(stored?.nexusWaLastProspectId || 0) === pid) {
    patch.nexusWaLastProspectId = null
  }
  if (Number(stored?.nexusLiLastProspectId || 0) === pid) {
    patch.nexusLiLastProspectId = null
  }
  const liWatch = stored?.[WATCH_KEY]
  if (liWatch && Number(liWatch?.prospectId || 0) === pid) {
    patch[WATCH_KEY] = null
    patch.nexusLiWatchUntil = 0
  }
  if (nextWa.length === 0) {
    patch.nexusWaWatchUntil = 0
  }
  await chrome.storage.local.set(patch)
  return { ok: true, prospectId: pid, waRemaining: nextWa.length }
}

async function setWhatsAppPending({
  sendUrl,
  message,
  prospectId,
  phoneDigits,
  prospectName,
  skipAutoPaste = false,
}) {
  const text = String(message || '').trim()
  const pid = prospectId ? Number(prospectId) : null
  if (!pid || !text) {
    return { ok: false, error: 'Faltan prospecto o mensaje WhatsApp.' }
  }
  const digits = String(phoneDigits || '').replace(/\D/g, '')
  const name = String(prospectName || '').trim()
  await upsertWaWatchTargetBg({
    prospectId: pid,
    phoneDigits: digits,
    prospectName: name,
    outboundText: text,
  })
  await chrome.storage.local.set({
    [WA_PENDING_KEY]: {
      prospectId: pid,
      phoneDigits: digits,
      prospectName: name,
      sendUrl: String(sendUrl || ''),
      message: text,
      messagePrefix: text.slice(0, 240),
      messageHash: hashText(text),
      since: Date.now(),
      pastedAt: Date.now(),
      pasteDone: false,
      pasteClaimed: false,
      pasteInserted: false,
      // El content script aislado NO pega: background pega en MAIN world.
      skipAutoPaste: Boolean(skipAutoPaste),
    },
    nexusWaLastChatPhone: digits,
    nexusWaLastProspectId: pid,
    nexusWaLastProspectName: name,
    nexusWaWatchUntil: Date.now() + 2 * 60 * 60 * 1000,
  })
  return { ok: true }
}

async function armOpenWhatsApp({ sendUrl, prospectId, message, phoneDigits, prospectName }) {
  const text = String(message || '').trim()
  const pid = prospectId ? Number(prospectId) : null
  const digits = String(phoneDigits || '').replace(/\D/g, '')
  const name = String(prospectName || '').trim()

  // Nunca ?text= en la URL: WhatsApp Web trunca el query y deja un borrador incompleto.
  // Abrimos el chat limpio y pegamos el texto completo en MAIN (Lexical).
  // El content script aislado NO pega (skipAutoPaste).
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
      prospectName: name,
      skipAutoPaste: true,
    })
    void armWhatsAppInboundWatchBurst()
  }

  let tabs = []
  try {
    tabs = await chrome.tabs.query({ url: ['https://web.whatsapp.com/*'] })
  } catch {
    tabs = []
  }
  let tab =
    (tabs || []).find((t) => t.id && !/nexus_wa_watch=/i.test(String(t.url || ''))) ||
    (tabs || []).find((t) => t.id) ||
    null

  const alreadyOnWa = Boolean(tab?.id)
  if (tab?.id) {
    await chrome.tabs.update(tab.id, { active: true, url })
  } else {
    tab = await chrome.tabs.create({ url, active: true })
  }

  if (!tab?.id || !text) return { ok: true, tabId: tab?.id, mode: 'whatsapp-web' }

  await waitForTabReady(tab.id, alreadyOnWa ? 4000 : 12000).catch(() => {})
  const ready = await waitForWhatsAppComposer(tab.id, alreadyOnWa ? 8000 : 15000)
  if (ready === false) {
    notifyAssistStatus(
      tab.id,
      'Iniciá sesión en WhatsApp Web una vez (QR). Después Nexus reusa esa sesión.',
      'warn',
    )
    return { ok: true, tabId: tab.id, mode: 'whatsapp-web', pasted: false }
  }

  await sleep(400)

  let filled = await readWhatsAppComposerMatch(tab.id, text)
  if (!filled) {
    filled = await pasteWhatsAppMainWorldOnce(tab.id, text)
  }

  if (filled) {
    try {
      const stored = await chrome.storage.local.get(WA_PENDING_KEY)
      const pending = stored?.[WA_PENDING_KEY]
      if (pending) {
        pending.pasteDone = true
        pending.pasteInserted = true
        pending.pasteClaimed = true
        await chrome.storage.local.set({ [WA_PENDING_KEY]: pending })
      }
    } catch {
      /* ignore */
    }
    notifyAssistStatus(tab.id, 'Mensaje listo en el renglón — enviá con Enter.', 'success')
  } else {
    notifyAssistStatus(
      tab.id,
      'No pude cargar el texto solo: está en el portapapeles — Ctrl+V una vez.',
      'warn',
    )
  }

  return { ok: true, tabId: tab?.id, mode: 'whatsapp-web', pasted: filled }
}

/** True solo si el composer tiene el borrador completo (no un truncado de ?text=). */
function whatsappComposerLooksComplete(curRaw, wantRaw) {
  const cur = String(curRaw || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  const w = String(wantRaw || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  if (!cur || !w) return false
  if (cur === w) return true
  // Drift menor de whitespace/emoji; nunca aceptar un prefijo corto.
  if (cur.includes(w) && cur.length <= w.length + 12) return true
  if (w.includes(cur)) return false
  const minLen = Math.max(24, Math.floor(w.length * 0.92))
  return cur.length >= minLen && Math.abs(cur.length - w.length) <= 12 && cur.includes(w.slice(0, 40))
}

/** Lee el composer en MAIN world y compara con el borrador. */
async function readWhatsAppComposerMatch(tabId, text) {
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
          document.querySelector('footer [contenteditable="true"][data-tab="10"]') ||
          document.querySelector('footer div[contenteditable="true"]')
        if (!el) return { ok: false, cur: '' }
        const cur = String(el.innerText || el.textContent || '')
        return { ok: true, cur }
      },
      args: [want],
    })
    const row = injected?.[0]?.result
    if (!row?.ok) return false
    return whatsappComposerLooksComplete(row.cur, want)
  } catch {
    return false
  }
}

/**
 * Pegado único en world MAIN (Lexical). selectAll + delete + ClipboardEvent;
 * si quedó corto/vacío, insertText con el borrador completo.
 */
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
          document.querySelector('#main .copyable-area [contenteditable="true"][role="textbox"]') ||
          document.querySelector('footer [contenteditable="true"][data-tab="10"]') ||
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

async function waitForWhatsAppComposer(tabId, timeoutMs) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    const st = await chrome.tabs
      .sendMessage(tabId, { type: 'NEXUS_WA_COMPOSER_READY' })
      .catch(() => null)
    if (st && st.loggedIn === false) return false
    if (st?.ready) return true

    // Fallback MAIN: content script puede no estar listo tras /send?phone=
    try {
      const injected = await chrome.scripting.executeScript({
        target: { tabId },
        world: 'MAIN',
        func: () => {
          const qr = document.querySelector('canvas[aria-label*="QR" i], div[data-ref] canvas')
          const composer =
            document.querySelector('#main footer [contenteditable="true"]') ||
            document.querySelector('footer [contenteditable="true"][role="textbox"]') ||
            document.querySelector('footer div[contenteditable="true"]')
          const pane = document.querySelector('#pane-side, [data-testid="chat-list"]')
          if (qr && !composer && !pane) return { loggedIn: false, ready: false }
          return { loggedIn: Boolean(pane || composer), ready: Boolean(composer) }
        },
      })
      const r = injected?.[0]?.result
      if (r && r.loggedIn === false) return false
      if (r?.ready) return true
    } catch {
      /* ignore */
    }

    await sleep(250)
  }
  return null
}

async function handleWhatsAppOutboundSent({ prospectId }) {
  const auth = await getAuth()
  if (!auth) {
    return { ok: false, error: 'Nexus no está autenticado. Abrí Nexus y logueate.' }
  }
  const pid = prospectId ? Number(prospectId) : null
  if (!pid) return { ok: false, error: 'Falta prospectId' }

  const res = await fetch(`${auth.apiBaseUrl}/prospects/${pid}/whatsapp-assisted/mark-sent`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${auth.token}` },
  })

  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    if (res.status === 400 && /no hay mensaje|pendiente/i.test(errText)) {
      await chrome.storage.local.remove(WA_PENDING_KEY)
      notifyNexusTabs({ type: 'NEXUS_WHATSAPP_SENT_REGISTERED', prospectId: pid })
      return { ok: true, already: true }
    }
    return { ok: false, error: `API ${res.status}: ${errText.slice(0, 200)}` }
  }

  await res.json().catch(() => ({}))
  await chrome.storage.local.remove(WA_PENDING_KEY)
  notifyNexusTabs({ type: 'NEXUS_WHATSAPP_SENT_REGISTERED', prospectId: pid })
  try {
    const st = await chrome.storage.local.get([
      'nexusWaLastChatPhone',
      'nexusWaLastProspectName',
      'nexusWaLastOutboundText',
    ])
    await upsertWaWatchTargetBg({
      prospectId: pid,
      phoneDigits: st?.nexusWaLastChatPhone || '',
      prospectName: st?.nexusWaLastProspectName || '',
      outboundText: st?.nexusWaLastOutboundText || '',
    })
  } catch {
    /* ignore */
  }
  void armWhatsAppInboundWatchBurst()
  return { ok: true, prospectId: pid }
}



async function resolveProspectId(auth, profileSlug) {
  const slugNorm = normalizeLiSlugBg(profileSlug)
  if (!slugNorm || slugNorm === 'unknown' || slugNorm === 'watched') {
    return null
  }

  const stored = await chrome.storage.local.get([
    WATCH_KEY,
    'nexusLiLastProspectId',
    'nexusLiLastProfileSlug',
    'nexusLiWatchUntil',
  ])
  const watch = stored?.[WATCH_KEY]
  const watchUntil = Number(stored?.nexusLiWatchUntil || 0)
  const watching = !watchUntil || Date.now() < watchUntil
  const watchSlugNorm = normalizeLiSlugBg(watch?.profileSlug || stored?.nexusLiLastProfileSlug || '')

  const q = encodeURIComponent(`https://www.linkedin.com/in/${slugNorm}/`)
  let apiId = null
  try {
    const res = await fetch(`${auth.apiBaseUrl}/prospects/resolve-linkedin?url=${q}`, {
      headers: { Authorization: `Bearer ${auth.token}` },
    })
    if (res.ok) {
      const data = await res.json()
      apiId = data?.prospect_id ? Number(data.prospect_id) : null
    }
  } catch {
    apiId = null
  }

  // LI-SAFE: nunca atribuir por “watch” sin slug coincidente.
  if (LI_SAFE_NO_PROFILE_PROBE) {
    if (watching && watch?.prospectId && watchSlugNorm && slugNorm === watchSlugNorm) {
      const wid = Number(watch.prospectId)
      if (apiId && apiId !== wid) return null
      return wid
    }
    return apiId
  }

  // Legacy (LI_SAFE off): watch sticky.
  if (watching && watch?.prospectId) {
    if (!slugNorm || !watchSlugNorm || slugNorm === watchSlugNorm) {
      return Number(watch.prospectId)
    }
  }
  if (watching && stored?.nexusLiLastProspectId && slugNorm && slugNorm === watchSlugNorm) {
    return Number(stored.nexusLiLastProspectId)
  }
  return apiId
}

function normalizeLiSlugBg(raw) {
  let s = String(raw || '').trim()
  try {
    s = decodeURIComponent(s)
  } catch {
    /* ignore */
  }
  s = s.toLowerCase()
  try {
    s = s.normalize('NFD').replace(/\p{M}/gu, '')
  } catch {
    /* ignore */
  }
  return s
}



function notifyNexusTabs(payload) {

  chrome.tabs.query({ url: ['http://127.0.0.1/*', 'http://localhost/*'] }, (tabs) => {

    for (const tab of tabs || []) {

      if (!tab.id) continue

      chrome.tabs.sendMessage(tab.id, payload).catch(() => {})

    }

  })

}



async function runAssistFn(tabId, fnName, args = [], worlds = ['MAIN', 'ISOLATED']) {
  const order = Array.isArray(worlds) && worlds.length ? worlds : ['MAIN', 'ISOLATED']
  let lastError = null

  for (const world of order) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [UTILS_FILE],
        world,
      })
      const injection = await chrome.scripting.executeScript({
        target: { tabId },
        world,
        func: async (name, fnArgs) => {
          const api = window.__NEXUS_LI_ASSIST__
          if (!api || typeof api[name] !== 'function') {
            return { __nexusMissing: true }
          }
          const value = await api[name](...fnArgs)
          // findComposeBox devuelve un Element no serializable → marcar presencia.
          if (name === 'findComposeBox') {
            return Boolean(value)
          }
          return value
        },
        args: [fnName, args],
      })
      const result = injection?.[0]?.result
      if (result && result.__nexusMissing) continue
      // false / "none" pueden ser fallo del world; probar el siguiente.
      if (fnName === 'pasteComposerMessage' && result === false) continue
      if (fnName === 'findComposeBox' && result === false) continue
      if (fnName === 'extractAssistTarget' && result && result.method === 'none' && !result.composeUrl) {
        continue
      }
      if (result !== null && result !== undefined) return result
    } catch (err) {
      lastError = err
    }
  }

  if (lastError) {
    console.warn('[Nexus LI] runAssistFn', fnName, lastError)
  }
  return null
}



function waitForTabReady(tabId, timeoutMs) {
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      chrome.tabs.onUpdated.removeListener(onUpdated)
      clearTimeout(timer)
      resolve()
    }

    const onUpdated = (id, info) => {
      if (id === tabId && info.status === 'complete') {
        finish()
      }
    }

    chrome.tabs.onUpdated.addListener(onUpdated)
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError) {
        finish()
        return
      }
      if (tab?.status === 'complete') {
        finish()
      }
    })

    const timer = setTimeout(finish, timeoutMs)
  })
}

/** LinkedIn SPA: status=complete llega antes del top card. Esperar h1 / nombre. */
async function waitForLinkedInProfileHydrated(tabId, timeoutMs = 20000) {
  const id = Number(tabId || 0)
  if (!id) return false
  const deadline = Date.now() + Math.max(2000, Number(timeoutMs) || 20000)
  while (Date.now() < deadline) {
    try {
      const injected = await chrome.scripting.executeScript({
        target: { tabId: id },
        world: 'MAIN',
        func: () => {
          const href = String(location.href || '')
          if (/\/login|\/authwall|\/checkpoint/i.test(href)) return 'authwall'
          const h1 =
            document.querySelector('main h1') ||
            document.querySelector('h1') ||
            document.querySelector('[data-anonymize="person-name"]')
          const name = (h1?.textContent || '').trim()
          if (name.length > 1) return 'ready'
          return 'loading'
        },
      })
      const state = injected?.[0]?.result
      if (state === 'ready') return true
      if (state === 'authwall') return false
    } catch {
      /* tab not ready */
    }
    await sleep(400)
  }
  return false
}



function sleep(ms) {

  return new Promise((resolve) => setTimeout(resolve, ms))

}



function slugFromProfileUrl(url) {

  try {

    const path = new URL(url).pathname.toLowerCase()

    const idx = path.indexOf('/in/')

    if (idx < 0) return null

    return decodeURIComponent(path.slice(idx + 4).split('/')[0])

  } catch {

    return null

  }

}



function hashText(text) {

  const base = String(text || '').trim()

  let hash = 0

  for (let i = 0; i < base.length; i += 1) {

    hash = (hash << 5) - hash + base.charCodeAt(i)

    hash |= 0

  }

  return String(Math.abs(hash))

}



function normalizeProfileUrl(raw) {

  const value = String(raw || '').trim()

  if (!value) {

    return null

  }

  try {

    const parsed = new URL(value.startsWith('http') ? value : `https://${value}`)

    if (!parsed.hostname.includes('linkedin.com')) {

      return null

    }

    const path = decodeURIComponent(parsed.pathname.replace(/\/+$/, ''))

    if (!path.startsWith('/in/')) {

      return null

    }

    return `https://www.linkedin.com${path}/`

  } catch {

    return null

  }

}



const INBOUND_ALARM = 'nexusInboundPoll'
const CONNECT_PROBE_ALARM = 'nexusConnectProbe'
/** Legacy: se limpia; ya no sondeamos aceptación post-Contactar. */
const INVITE_ACCEPT_ALARM = 'nexusInviteAcceptProbe'

chrome.alarms.create(INBOUND_ALARM, { periodInMinutes: 1 })
// checking: cada ~1 min; cooldown de fallo ~25s → varias lecturas dentro de 120s.
void chrome.alarms.clear(CONNECT_PROBE_ALARM).then(() => {
  chrome.alarms.create(CONNECT_PROBE_ALARM, { periodInMinutes: 1 })
})
void chrome.alarms.clear(INVITE_ACCEPT_ALARM)

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(INBOUND_ALARM, { periodInMinutes: 1 })
  void chrome.alarms.clear(CONNECT_PROBE_ALARM).then(() => {
    chrome.alarms.create(CONNECT_PROBE_ALARM, { periodInMinutes: 1 })
  })
  void chrome.alarms.clear(INVITE_ACCEPT_ALARM)
    void chrome.storage.local.remove([
    PROBE_ATTEMPT_KEY,
    PROBE_MAX_ATTEMPTS_KEY,
    PROBE_TABS_KEY,
    PROBE_META_KEY,
    'nexusLiProbeResolved',
  ])
  nexusOwnedProbeTabs.clear()
  void probePendingLinkedInConnections()
  void reviveInboundWatchersAfterExtensionReload()
})

chrome.runtime.onStartup.addListener(() => {
  void chrome.alarms.clear(CONNECT_PROBE_ALARM).then(() => {
    chrome.alarms.create(CONNECT_PROBE_ALARM, { periodInMinutes: 1 })
  })
  void chrome.alarms.clear(INVITE_ACCEPT_ALARM)
  void chrome.storage.local.remove([
    PROBE_ATTEMPT_KEY,
    PROBE_MAX_ATTEMPTS_KEY,
    PROBE_TABS_KEY,
    PROBE_META_KEY,
    'nexusLiProbeResolved',
  ])
  nexusOwnedProbeTabs.clear()
  void probePendingLinkedInConnections()
  void reviveInboundWatchersAfterExtensionReload()
})

async function reviveInboundWatchersAfterExtensionReload() {
  try {
    const stored = await chrome.storage.local.get([
      WATCH_KEY,
      'nexusLiWatchUntil',
      'nexusLiLastProspectId',
      'nexusLiLastProfileSlug',
    ])
    const until = Number(stored?.nexusLiWatchUntil || 0)
    const watching = until && Date.now() < until
    if (watching) {
      void armLinkedInInboundWatchBurst()
      const watch = stored?.[WATCH_KEY] || {}
      void notifyLinkedInWatchArmed({
        profileSlug: watch.profileSlug || stored?.nexusLiLastProfileSlug || null,
        prospectId: watch.prospectId || stored?.nexusLiLastProspectId || null,
      })
    } else {
      void pollLinkedInTabsForInbound()
    }
  } catch (err) {
    console.warn('[Nexus] revive inbound failed', err?.message || err)
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  const name = String(alarm.name || '')
  if (
    name === INBOUND_ALARM ||
    name.startsWith('nexus-wa-inbound-burst-') ||
    name.startsWith('nexus-li-inbound-burst-')
  ) {
    if (name === INBOUND_ALARM || name.startsWith('nexus-li-inbound-burst-')) {
      void pollLinkedInTabsForInbound()
    }
    if (name === INBOUND_ALARM || name.startsWith('nexus-wa-inbound-burst-')) {
      void pollWhatsAppTabsForInbound()
    }
  }
  if (alarm.name === CONNECT_PROBE_ALARM) {
    void probePendingLinkedInConnections()
  }
})

async function pollLinkedInTabsForInbound({ force = false } = {}) {
  // LI-SAFE / LI-IN: Messaging page O overlay de chats. Sin Voyager desde SW.
  if (LI_SAFE_NO_PROFILE_PROBE && !force) {
    const now = Date.now()
    let watching = false
    try {
      const st = await chrome.storage.local.get(['nexusLiWatchUntil', WATCH_KEY])
      const until = Number(st?.nexusLiWatchUntil || 0)
      watching = Boolean(st?.[WATCH_KEY]?.prospectId || st?.[WATCH_KEY]?.profileSlug) && (!until || now < until)
    } catch {
      watching = false
    }
    const gap = watching ? LI_INBOUND_WATCH_GAP_MS : LI_INBOUND_MIN_GAP_MS
    if (now - lastLiInboundPollAt < gap) {
      return { ok: true, skipped: true, reason: 'li_safe_rate_limit', candidates: 0, messagingTabs: 0 }
    }
  }

  let tabs = []
  try {
    tabs = await chrome.tabs.query({ url: ['https://www.linkedin.com/*'] })
  } catch {
    tabs = []
  }
  // Incluir /messaging y también feed/in (overlay de chats). El content script no-opea si no hay UI de mensajes.
  if (!tabs.length) {
    return {
      ok: false,
      reason: 'no_linkedin_tab',
      candidates: 0,
      messagingTabs: 0,
    }
  }

  if (LI_SAFE_NO_PROFILE_PROBE) lastLiInboundPollAt = Date.now()

  let anyOk = false
  let candidates = 0
  let messagingContexts = 0
  const tabResults = []
  for (const tab of tabs || []) {
    if (!tab.id) continue
    const url = String(tab.url || '')
    if (!/linkedin\.com/i.test(url)) continue
    // Skip chrome internal / login walls quickly by host only.
    const ok = await ensureInboundContentScript(tab.id)
    if (!ok) {
      tabResults.push({ tabId: tab.id, ok: false, reason: 'inject_failed' })
      continue
    }
    try {
      const ping = await chrome.tabs.sendMessage(tab.id, { type: 'NEXUS_PING_INBOUND' })
      if (!ping?.messaging) {
        tabResults.push({ tabId: tab.id, ok: true, skipped: 'no_messaging_ui', candidates: 0 })
        continue
      }
      messagingContexts += 1
      const res = await chrome.tabs.sendMessage(tab.id, {
        type: 'NEXUS_POLL_INBOUND',
        liSafe: Boolean(LI_SAFE_NO_PROFILE_PROBE),
        force: Boolean(force),
      })
      if (res?.ok) anyOk = true
      const n = Number(res?.candidates || 0)
      if (Number.isFinite(n)) candidates += n
      tabResults.push({
        tabId: tab.id,
        ok: Boolean(res?.ok),
        candidates: n,
        watching: Boolean(res?.watching),
        threadOpen: Boolean(res?.threadOpen),
        watchSlug: res?.watchSlug || null,
        skipped: res?.skipped || null,
        reason: res?.reason || null,
        domDiag: res?.domDiag || null,
      })
    } catch (err) {
      console.warn('[Nexus] inbound poll failed tab', tab.id, err?.message || err)
      tabResults.push({ tabId: tab.id, ok: false, reason: String(err?.message || err) })
    }
  }
  const result = {
    ok: anyOk,
    candidates,
    messagingTabs: messagingContexts,
    tabResults,
    reason: messagingContexts ? null : 'no_messaging_context',
  }
  try {
    await chrome.storage.local.set({
      nexusLiLastInboundPoll: {
        at: Date.now(),
        ...result,
      },
    })
  } catch {
    /* ignore */
  }
  return result
}

async function handlePollInboundNow({
  profileUrl,
  profileSlug,
  prospectId,
  prospectName,
  outboundText,
} = {}) {
  const auth = await getAuth()
  if (!auth?.token) {
    return {
      ok: false,
      reason: 'no_auth',
      error: 'Nexus no está autenticado en la extensión. Abrí Nexus logueado y recargá.',
      candidates: 0,
      messagingTabs: 0,
    }
  }

  // Armar / refrescar watch del prospecto pedido antes de leer.
  if (prospectId || profileUrl || profileSlug) {
    await handleArmInboundWatch({
      profileUrl,
      profileSlug,
      prospectId,
      prospectName,
      outboundText,
    })
  }

  lastLiInboundPollAt = 0
  const poll = await pollLinkedInTabsForInbound({ force: true })
  if (poll?.reason === 'no_messaging_context' || poll?.reason === 'no_messaging_tab') {
    return {
      ok: false,
      reason: 'no_messaging_context',
      error:
        'No hay UI de chats visible. Abrí Messaging o el overlay de mensajes en LinkedIn.',
      candidates: 0,
      messagingTabs: 0,
    }
  }
  return {
    ok: Boolean(poll?.ok),
    reason: poll?.reason || null,
    candidates: Number(poll?.candidates || 0),
    messagingTabs: Number(poll?.messagingTabs || 0),
    tabResults: poll?.tabResults || [],
    insertedHint: Number(poll?.candidates || 0) > 0,
  }
}

function isLinkedInMessagingTabUrl(url) {
  try {
    const u = new URL(String(url || ''))
    if (!u.hostname.includes('linkedin.com')) return false
    return u.pathname.toLowerCase().includes('/messaging')
  } catch {
    return /linkedin\.com\/+messaging/i.test(String(url || ''))
  }
}

async function ensureInboundContentScript(tabId) {
  const id = Number(tabId || 0)
  if (!id) return false
  try {
    const ping = await chrome.tabs.sendMessage(id, { type: 'NEXUS_PING_INBOUND' })
    if (ping?.ok) return true
  } catch {
    /* need inject */
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId: id },
      files: ['linkedin-inbound-utils.js', 'content-linkedin-inbound.js'],
    })
    await sleep(250)
    const ping2 = await chrome.tabs.sendMessage(id, { type: 'NEXUS_PING_INBOUND' })
    return Boolean(ping2?.ok)
  } catch (err) {
    console.warn('[Nexus] inbound inject failed', id, err?.message || err)
    return false
  }
}

async function notifyLinkedInWatchArmed({ profileSlug, prospectId, prospectName } = {}) {
  const tabs = await chrome.tabs.query({ url: ['https://www.linkedin.com/*'] })
  let notified = 0
  for (const tab of tabs || []) {
    if (!tab.id) continue
    await ensureInboundContentScript(tab.id)
    try {
      const ping = await chrome.tabs.sendMessage(tab.id, { type: 'NEXUS_PING_INBOUND' })
      if (!ping?.messaging) continue
      await chrome.tabs.sendMessage(tab.id, {
        type: 'NEXUS_LI_WATCH_ARMED',
        profileSlug: profileSlug || null,
        prospectId: prospectId || null,
        prospectName: prospectName || null,
      })
      notified += 1
    } catch {
      /* ignore */
    }
  }
  if (!notified) {
    notifyNexusTabs({
      type: 'NEXUS_LI_WATCH_NEEDS_LINKEDIN',
      prospectId: prospectId || null,
      message:
        'Abrí el chat de LinkedIn (Messaging o el globo de mensajes) para detectar respuestas.',
    })
  }
}

async function injectWaNotificationHook(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: () => {
      if (window.__NEXUS_WA_NOTIFY_HOOK__) return
      window.__NEXUS_WA_NOTIFY_HOOK__ = true
      const Native = window.Notification
      if (!Native) return
      function Wrapped(title, opts) {
        try {
          window.postMessage(
            {
              type: 'NEXUS_WA_NATIVE_NOTIFICATION',
              title: String(title || ''),
              body: String((opts && opts.body) || ''),
              tag: String((opts && opts.tag) || ''),
            },
            '*',
          )
        } catch {
          /* ignore */
        }
        try {
          return new Native(title, opts)
        } catch {
          return undefined
        }
      }
      Wrapped.permission = Native.permission
      Wrapped.requestPermission = Native.requestPermission.bind(Native)
      try {
        window.Notification = Wrapped
      } catch {
        /* ignore */
      }
    },
  })
}

const WA_CONFIG_KEY = 'nexusWaExtConfig'
const WA_CONFIG_AT_KEY = 'nexusWaExtConfigAt'
const WA_CONFIG_TTL_MS = 60 * 60 * 1000
const EXT_VERSION = '0.18.74'

/** Lee config OTA (JSON only). Cache 1h. */
async function fetchWaExtConfig(force = false) {
  const stored = await chrome.storage.local.get([WA_CONFIG_KEY, WA_CONFIG_AT_KEY])
  const at = Number(stored?.[WA_CONFIG_AT_KEY] || 0)
  if (!force && stored?.[WA_CONFIG_KEY] && Date.now() - at < WA_CONFIG_TTL_MS) {
    return stored[WA_CONFIG_KEY]
  }
  const auth = await getAuth()
  if (!auth?.token) return stored?.[WA_CONFIG_KEY] || null
  try {
    const res = await fetch(`${auth.apiBaseUrl}/extension/wa-config`, {
      headers: { Authorization: `Bearer ${auth.token}` },
    })
    if (!res.ok) return stored?.[WA_CONFIG_KEY] || null
    const data = await res.json()
    await chrome.storage.local.set({
      [WA_CONFIG_KEY]: data,
      [WA_CONFIG_AT_KEY]: Date.now(),
    })
    return data
  } catch {
    return stored?.[WA_CONFIG_KEY] || null
  }
}

async function postWaExtTelemetry(payload) {
  try {
    const auth = await getAuth()
    if (!auth?.token) return
    await fetch(`${auth.apiBaseUrl}/extension/wa-telemetry`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${auth.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        extension_version: EXT_VERSION,
        ...payload,
      }),
    })
  } catch {
    /* ignore */
  }
}

/** Lee inbound de chats vigilados vía Store interno (MAIN), sin abrir chats. */
async function readWhatsAppStoreWatched(tabId, watchList) {
  const id = Number(tabId || 0)
  if (!id) return { ok: false, error: 'no_tab', rows: [] }
  const watches = (Array.isArray(watchList) ? watchList : [])
    .map((w) => ({
      prospectId: Number(w?.prospectId || 0) || 0,
      phone: String(w?.phone || '').replace(/\D/g, ''),
      name: String(w?.name || '').trim(),
      outboundText: String(w?.outboundText || '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 240),
    }))
    .filter((w) => w.phone.length >= 8 || w.prospectId || w.name)
  if (!watches.length) return { ok: true, rows: [], diag: { emptyWatch: true } }

  const config = (await fetchWaExtConfig()) || {}
  if (config.storeEnabled === false) {
    return { ok: false, error: 'store_disabled', rows: [], diag: { error: 'store_disabled' } }
  }

  try {
    await chrome.scripting.executeScript({
      target: { tabId: id },
      world: 'MAIN',
      files: ['wa-store-reader.js'],
    })
  } catch (err) {
    return { ok: false, error: `inject:${String(err?.message || err)}`, rows: [] }
  }

  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId: id },
      world: 'MAIN',
      func: async (watchArg, configArg) => {
        const fn = globalThis.__NEXUS_WA_READ_WATCHED__
        if (typeof fn !== 'function') return { ok: false, error: 'reader_missing', rows: [] }
        return fn(watchArg, configArg)
      },
      args: [watches, config],
    })
    return injected?.[0]?.result || { ok: false, error: 'no_result', rows: [] }
  } catch (err) {
    return { ok: false, error: String(err?.message || err), rows: [] }
  }
}

/** Abre chat WA por teléfono SOLO si la pestaña/ventana NO están enfocadas. */
async function openWhatsAppChatQuiet(phoneDigits) {
  const phone = String(phoneDigits || '').replace(/\D/g, '')
  if (phone.length < 8) return { ok: false, error: 'phone' }

  let tabs = []
  try {
    tabs = await chrome.tabs.query({ url: ['https://web.whatsapp.com/*'] })
  } catch {
    tabs = []
  }
  const tab = (tabs || []).find((t) => t.id) || null
  if (!tab?.id) return { ok: false, error: 'no_wa_tab' }

  // Nunca navegar si el usuario está en esa pestaña / ventana (roba foco).
  if (tab.active) {
    let winFocused = false
    try {
      const win = await chrome.windows.get(tab.windowId)
      winFocused = Boolean(win?.focused)
    } catch {
      winFocused = true
    }
    if (winFocused) return { ok: false, skipped: 'tab_focused' }
  }

  const stored = await chrome.storage.local.get(['nexusWaQuietOpenAt'])
  const map = stored?.nexusWaQuietOpenAt && typeof stored.nexusWaQuietOpenAt === 'object'
    ? stored.nexusWaQuietOpenAt
    : {}
  const now = Date.now()
  if (now - Number(map[phone] || 0) < 90 * 1000) {
    return { ok: true, skipped: 'cooldown' }
  }

  // Ya estamos en /send?phone= de este número → no re-navegar.
  const cur = String(tab.url || '')
  if (cur.includes(`phone=${phone}`) || cur.includes(`phone=${encodeURIComponent(phone)}`)) {
    return { ok: true, skipped: 'already_on_chat' }
  }

  map[phone] = now
  await chrome.storage.local.set({ nexusWaQuietOpenAt: map })

  const url = `https://web.whatsapp.com/send?phone=${phone}`
  try {
    // Navegar in-page vía scripting: evita activar la pestaña.
    const injected = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (targetUrl, targetPhone) => {
        try {
          if (!document.hidden && document.hasFocus()) {
            return { ok: false, skipped: 'document_focused' }
          }
          const u = new URL(location.href)
          if (u.searchParams.get('phone') === targetPhone) {
            return { ok: true, already: true }
          }
          location.assign(targetUrl)
          return { ok: true, navigated: true }
        } catch (e) {
          return { ok: false, error: String(e?.message || e) }
        }
      },
      args: [url, phone],
    })
    const result = injected?.[0]?.result || { ok: false }
    return { ok: Boolean(result?.ok), ...result, tabId: tab.id, phone }
  } catch (err) {
    return { ok: false, error: String(err?.message || err) }
  }
}

async function armWhatsAppInboundWatchBurst() {
  await chrome.storage.local.set({
    nexusWaWatchUntil: Date.now() + 2 * 60 * 60 * 1000,
  })
  for (let i = 1; i <= 30; i += 1) {
    chrome.alarms.create(`nexus-wa-inbound-burst-${i}`, { delayInMinutes: i })
  }
  // NO abrir otra pestaña WA (rompe la sesión con “Usar aquí”).
  // Vigilar en la misma sesión que ya tiene el usuario.
  void pollWhatsAppTabsForInbound()
}

/** Tras marcar enviado LinkedIn: poll inmediato + alarms (~30 min). */
async function armLinkedInInboundWatchBurst() {
  // Permitir polls del burst pese al rate-limit LI-IN.
  lastLiInboundPollAt = 0
  await chrome.storage.local.set({
    nexusLiWatchUntil: Date.now() + LI_WATCH_MS,
  })
  for (let i = 1; i <= 30; i += 1) {
    chrome.alarms.create(`nexus-li-inbound-burst-${i}`, { delayInMinutes: i })
  }
  // Varios polls seguidos al marcar enviado (la respuesta a veces llega en segundos).
  const run = () => {
    lastLiInboundPollAt = 0
    void pollLinkedInTabsForInbound()
  }
  run()
  setTimeout(run, 1500)
  setTimeout(run, 5000)
  setTimeout(run, 15000)
  setTimeout(run, 45000)
}

/**
 * Nunca crear una 2ª pestaña de WhatsApp Web: WhatsApp echa la sesión anterior.
 * Reusa la pestaña WA que ya esté abierta (sin navegarla para vigilar).
 */
async function ensureWhatsAppWatcherTab() {
  let tabs = []
  try {
    tabs = await chrome.tabs.query({ url: ['https://web.whatsapp.com/*'] })
  } catch {
    tabs = []
  }
  // Preferir pestaña normal del usuario (sin nexus_wa_watch).
  const normal = (tabs || []).find((t) => t.id && !/nexus_wa_watch=/i.test(String(t.url || '')))
  if (normal?.id) {
    await chrome.storage.local.set({ nexusWaWatcherTabId: normal.id })
    return normal.id
  }
  const any = (tabs || []).find((t) => t.id)
  if (any?.id) {
    await chrome.storage.local.set({ nexusWaWatcherTabId: any.id })
    return any.id
  }
  return null
}

/**
 * Vigila respuestas en la pestaña WA existente.
 * Camino principal: LISTA de chats (sin abrir el chat del prospecto).
 * Solo números/nombres en nexusWaWatchList (con los que Nexus habló).
 */
async function pollWhatsAppTabsForInbound() {
  const stored = await chrome.storage.local.get([
    'nexusWaLastProspectId',
    'nexusWaLastChatPhone',
    'nexusWaLastOutboundText',
    'nexusWaLastOutboundAt',
    'nexusWaLastProspectName',
    'nexusWaWatchUntil',
    WA_PENDING_KEY,
    WA_WATCH_LIST_KEY,
  ])
  let watchList = Array.isArray(stored?.[WA_WATCH_LIST_KEY]) ? stored[WA_WATCH_LIST_KEY] : []
  const now = Date.now()
  watchList = watchList.filter((x) => now - Number(x?.since || 0) < WA_WATCH_TTL_MS)

  // Sembrar desde sticky si la lista está vacía pero hay último envío.
  if (!watchList.length) {
    const phone = String(
      stored?.[WA_PENDING_KEY]?.phoneDigits || stored?.nexusWaLastChatPhone || '',
    ).replace(/\D/g, '')
    const pid = Number(
      stored?.[WA_PENDING_KEY]?.prospectId || stored?.nexusWaLastProspectId || 0,
    )
    if (phone.length >= 8 || pid) {
      await upsertWaWatchTargetBg({
        prospectId: pid,
        phoneDigits: phone,
        prospectName: stored?.nexusWaLastProspectName || stored?.[WA_PENDING_KEY]?.prospectName || '',
        outboundText: stored?.nexusWaLastOutboundText || stored?.[WA_PENDING_KEY]?.message || '',
      })
      const refreshed = await chrome.storage.local.get([WA_WATCH_LIST_KEY])
      watchList = Array.isArray(refreshed?.[WA_WATCH_LIST_KEY]) ? refreshed[WA_WATCH_LIST_KEY] : []
    }
  }

  const watchUntil = Number(stored?.nexusWaWatchUntil || 0)
  const recentOutbound = Number(stored?.nexusWaLastOutboundAt || 0)
  const watching =
    watchList.length > 0 ||
    (watchUntil && Date.now() < watchUntil) ||
    (recentOutbound && Date.now() - recentOutbound < 2 * 60 * 60 * 1000) ||
    Boolean(stored?.[WA_PENDING_KEY]?.prospectId) ||
    Boolean(stored?.nexusWaLastProspectId)

  if (!watching) return

  let tabs = []
  try {
    tabs = await chrome.tabs.query({ url: ['https://web.whatsapp.com/*'] })
  } catch {
    tabs = []
  }
  if (!tabs.length) return

  for (const tab of tabs) {
    if (!tab.id) continue
    // 1) Store interno primero (sin abrir chats / sin foco).
    if (watchList.length) {
      try {
        const storeRes = await readWhatsAppStoreWatched(tab.id, watchList)
        if (storeRes?.diag || storeRes?.error) {
          void postWaExtTelemetry({
            store_ok: Boolean(storeRes?.ok && storeRes?.diag && !storeRes.diag.error),
            store_source: storeRes?.diag?.source || null,
            store_error: storeRes?.diag?.error || storeRes?.error || null,
            chats: storeRes?.diag?.chats ?? null,
            matched: storeRes?.diag?.matched ?? null,
            inbound: storeRes?.diag?.inbound ?? null,
            candidates: Array.isArray(storeRes?.rows) ? storeRes.rows.length : 0,
            reason: 'bg-poll',
          })
        }
        if (storeRes?.ok && Array.isArray(storeRes.rows)) {
          for (const row of storeRes.rows) {
            const text = String(row?.text || '').trim()
            if (!text) continue
            const rowPhone = String(row?.phone || '').replace(/\D/g, '')
            const w = watchList.find(
              (x) =>
                (row.prospectId && Number(x.prospectId) === Number(row.prospectId)) ||
                (rowPhone && x.phone && String(x.phone).replace(/\D/g, '').slice(-10) === rowPhone.slice(-10)),
            )
            const ours = String(w?.outboundText || stored?.nexusWaLastOutboundText || '')
            if (isTextEchoOfOutbound(text, ours)) continue
            await handleWhatsAppInboundDetected({
              phoneDigits: rowPhone || w?.phone || '',
              message: text,
              whatsappMessageId: null,
              prospectId: row.prospectId || w?.prospectId || null,
            })
          }
        }
      } catch {
        /* ignore store errors; fallback below */
      }
    }

    // 2) Content script: lista + DOM.
    chrome.tabs
      .sendMessage(tab.id, { type: 'NEXUS_POLL_WA_INBOUND', reason: 'passive-list' })
      .catch(() => {})

    // Backup inyectado: escanear #pane-side aunque el content script falle.
    if (!watchList.length) continue
    let listScan = null
    try {
      const injected = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (watchArg) => {
          const root =
            document.querySelector('#pane-side') ||
            document.querySelector('#side') ||
            document.querySelector('[data-testid="chat-list"]') ||
            document.querySelector('div[aria-label*="Lista de chats" i]') ||
            document.querySelector('div[aria-label*="Chat list" i]')
          if (!root) return { ok: false, reason: 'no_list' }
          const stripAr = (d) => {
            const x = String(d || '').replace(/\D/g, '')
            if (x.startsWith('549') && x.length >= 12) return `54${x.slice(3)}`
            if (x.startsWith('54') && !x.startsWith('549') && x.length >= 11) return `549${x.slice(2)}`
            return x
          }
          const matchPhone = (a, b) => {
            const da = String(a || '').replace(/\D/g, '')
            const db = String(b || '').replace(/\D/g, '')
            if (!da || !db || da.length < 8 || db.length < 8) return false
            if (da === db) return true
            if (stripAr(da) === stripAr(db)) return true
            return da.length >= 10 && db.length >= 10 && da.slice(-10) === db.slice(-10)
          }
          const norm = (s) =>
            String(s || '')
              .normalize('NFD')
              .replace(/[\u0300-\u036f]/g, '')
              .toLowerCase()
              .replace(/[^\p{L}\p{N}\s]/gu, ' ')
              .replace(/\s+/g, ' ')
              .trim()
          const namesMatch = (a, b) => {
            const na = norm(a)
            const nb = norm(b)
            if (!na || !nb) return false
            if (na === nb) return true
            if (na.length >= 5 && nb.length >= 5 && (na.includes(nb) || nb.includes(na))) return true
            const pa = na.split(' ').filter((p) => p.length >= 3)
            const pb = nb.split(' ').filter((p) => p.length >= 3)
            if (!pa.length || !pb.length) return false
            if (pa.length >= 2 && pb.length >= 2) {
              const setB = new Set(pb)
              if (pa.filter((p) => setB.has(p)).length >= 2) return true
            }
            return pa.some((p) => pb.some((q) => p === q))
          }
          const clean = (raw) => {
            let t = String(raw || '')
              .replace(/\s+/g, ' ')
              .trim()
            t = t.replace(/^[\u2713\u2714✓✔]+\s*/g, '')
            t = t.replace(/^(t[uú]|you|vos)\s*:\s*/i, '')
            return t.trim()
          }
          const phoneFromDataId = (raw) => {
            const m = String(raw || '').match(/(\d{8,15})@(?:c\.us|s\.whatsapp\.net)/i)
            return m ? m[1] : ''
          }
          const rowUnread = (item) =>
            Boolean(
              item.querySelector('[data-testid="icon-unread-count"]') ||
                item.querySelector('[data-testid="icon-unread"]') ||
                item.querySelector('[data-testid="unread-count"]') ||
                item.querySelector('[data-testid*="unread" i]') ||
                item.querySelector('span[aria-label*="no leído" i]') ||
                item.querySelector('span[aria-label*="unread" i]'),
            )
          const out = []
          const items = root.querySelectorAll(
            '[data-testid="cell-frame-container"], div[role="listitem"], div[tabindex="-1"]',
          )
          for (const item of items) {
            const unread = rowUnread(item)
            const titleEl =
              item.querySelector('[data-testid="cell-frame-title"] span[title]') ||
              item.querySelector('[data-testid="cell-frame-title"] span') ||
              item.querySelector('span[title]')
            const title = String(titleEl?.getAttribute('title') || titleEl?.textContent || '')
              .replace(/\s+/g, ' ')
              .trim()
            let phone = phoneFromDataId(item.getAttribute?.('data-id')) || title.replace(/\D/g, '')
            if (!phone) {
              const withId = item.querySelector?.('[data-id]')
              phone = phoneFromDataId(withId?.getAttribute('data-id')) || phone
            }
            let watched = null
            for (const w of watchArg || []) {
              const wp = String(w.phone || '').replace(/\D/g, '')
              if (wp.length >= 8 && phone.length >= 8 && matchPhone(phone, wp)) {
                watched = w
                break
              }
              if (
                wp.length >= 8 &&
                title.replace(/\D/g, '').length >= 8 &&
                matchPhone(title.replace(/\D/g, ''), wp)
              ) {
                watched = w
                break
              }
              if (w.name && title && namesMatch(w.name, title)) {
                watched = w
                break
              }
            }
            if (!watched) continue
            const wp = String(watched.phone || '').replace(/\D/g, '')
            if (wp && (phone.length < 8 || !matchPhone(phone, wp))) phone = wp
            const previewEl =
              item.querySelector('[data-testid="cell-frame-secondary"] span.selectable-text') ||
              item.querySelector('[data-testid="cell-frame-secondary"] span[dir="ltr"]') ||
              item.querySelector('[data-testid="cell-frame-secondary"]') ||
              null
            const rawPrev = String(previewEl?.innerText || previewEl?.textContent || '')
            if (/^(t[uú]|you|vos)\s*:/i.test(rawPrev.trim())) continue
            const preview = clean(rawPrev)
            if (!preview) continue
            // No veto por last-msg-status: solo eco del outbound.
            const ours = String(watched.outboundText || '')
              .replace(/\s+/g, ' ')
              .trim()
              .toLowerCase()
            const pt = preview.toLowerCase()
            if (ours.length >= 12 && pt.length >= 16) {
              if (pt === ours || ours.includes(pt) || pt.includes(ours.slice(0, 32))) continue
            } else if (ours.length >= 12 && (pt === ours || pt.includes(ours.slice(0, 32)))) {
              continue
            }
            out.push({
              phone: phone || watched.phone,
              text: preview.slice(0, 500),
              prospectId: Number(watched.prospectId || 0) || null,
              unread: Boolean(unread),
            })
            if (out.length >= 12) break
          }
          return { ok: true, rows: out }
        },
        args: [watchList],
      })
      listScan = injected?.[0]?.result || null
    } catch (err) {
      listScan = { ok: false, reason: String(err?.message || err) }
    }
    if (!listScan?.ok || !Array.isArray(listScan.rows)) continue
    const stripArFind = (d) => {
      const x = String(d || '').replace(/\D/g, '')
      if (x.startsWith('549') && x.length >= 12) return `54${x.slice(3)}`
      if (x.startsWith('54') && !x.startsWith('549') && x.length >= 11) return `549${x.slice(2)}`
      return x
    }
    const samePhoneFind = (a, b) => {
      const da = String(a || '').replace(/\D/g, '')
      const db = String(b || '').replace(/\D/g, '')
      if (!da || !db || da.length < 8 || db.length < 8) return false
      if (da === db) return true
      if (stripArFind(da) === stripArFind(db)) return true
      return da.length >= 10 && db.length >= 10 && da.slice(-10) === db.slice(-10)
    }
    for (const row of listScan.rows) {
      const text = String(row?.text || '').trim()
      if (!text) continue
      const rowPhone = String(row?.phone || '').replace(/\D/g, '')
      const w = watchList.find(
        (x) =>
          (rowPhone && x.phone && samePhoneFind(x.phone, rowPhone)) ||
          (row.prospectId && Number(x.prospectId) === Number(row.prospectId)),
      )
      const ours = String(w?.outboundText || stored?.nexusWaLastOutboundText || '')
      if (isTextEchoOfOutbound(text, ours)) continue
      await handleWhatsAppInboundDetected({
        phoneDigits: rowPhone || w?.phone || '',
        message: text,
        whatsappMessageId: null,
        prospectId: row.prospectId || w?.prospectId || null,
      })
    }
  }
}

function isTextEchoOfOutbound(text, ours) {
  const t = String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .replace(/^(t[uú]|you|vos)\s*:\s*/i, '')
  const o = String(ours || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  if (!t) return true
  if (/^t[uú]:/i.test(t) || /^you:/i.test(t) || /^vos:/i.test(t)) return true
  if (!o || o.length < 8) return false
  if (t === o) return true
  const n = Math.min(48, t.length, o.length)
  if (n >= 16 && (t.slice(0, n) === o.slice(0, n) || t.includes(o.slice(0, n)) || o.includes(t.slice(0, n)))) {
    return true
  }
  // Substring solo con previews largos (evitar “ok”/“sí” dentro del outbound).
  if (t.length >= 16 && o.includes(t)) return true
  if (t.includes(o.slice(0, 50)) && t.length >= Math.min(o.length, 30)) return true
  if (o.includes(t) && t.length >= 20) return true
  return false
}

function textsAfterOurOutbound(texts, ours) {
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
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase()
    if (t.includes(head) || head.includes(t.slice(0, 40))) lastOurs = i
  }
  if (lastOurs < 0) return list.slice(-3)
  return list.slice(lastOurs + 1)
}

/** Pestañas abiertas solo para verificar grado 1/2/3 — siempre se cierran. */
/** @type {Map<number, { prospectId: number, slug: string }>} */
const nexusOwnedProbeTabs = new Map()
const PROBE_TABS_KEY = 'nexusProbeTabIds'
const PROBE_META_KEY = 'nexusProbeTabMeta'
/** Serializa probes: una sola pestaña a la vez. */
let probeChain = Promise.resolve()

function withProbeLock(fn) {
  const run = probeChain.then(fn, fn)
  probeChain = run.then(
    () => undefined,
    () => undefined,
  )
  return run
}

async function persistProbeTabs() {
  try {
    const ids = [...nexusOwnedProbeTabs.keys()]
    const meta = {}
    for (const [id, info] of nexusOwnedProbeTabs.entries()) {
      meta[String(id)] = info
    }
    await chrome.storage.local.set({ [PROBE_TABS_KEY]: ids, [PROBE_META_KEY]: meta })
  } catch {
    /* ignore */
  }
}

async function rememberProbeTab(tabId, prospectId = 0, slug = '') {
  const id = Number(tabId || 0)
  if (!id) return
  nexusOwnedProbeTabs.set(id, {
    prospectId: Number(prospectId || 0) || 0,
    slug: String(slug || '').toLowerCase(),
  })
  await persistProbeTabs()
}

async function forgetProbeTab(tabId) {
  const id = Number(tabId || 0)
  if (!id) return
  nexusOwnedProbeTabs.delete(id)
  await persistProbeTabs()
}

async function hydrateProbeTabsFromStorage() {
  try {
    const stored = await chrome.storage.local.get([PROBE_TABS_KEY, PROBE_META_KEY])
    const meta = stored?.[PROBE_META_KEY] || {}
    for (const id of stored?.[PROBE_TABS_KEY] || []) {
      const n = Number(id)
      if (!n || nexusOwnedProbeTabs.has(n)) continue
      const info = meta[String(n)] || {}
      nexusOwnedProbeTabs.set(n, {
        prospectId: Number(info.prospectId || 0) || 0,
        slug: String(info.slug || ''),
      })
    }
  } catch {
    /* ignore */
  }
}

async function lookupProbeProspectId({ tabId = 0, slug = '' } = {}) {
  await hydrateProbeTabsFromStorage()
  const id = Number(tabId || 0)
  if (id && nexusOwnedProbeTabs.has(id)) {
    const pid = Number(nexusOwnedProbeTabs.get(id)?.prospectId || 0)
    if (pid) return pid
  }
  const want = normalizeLiSlugBg(slug)
  if (!want) return null
  for (const info of nexusOwnedProbeTabs.values()) {
    if (normalizeLiSlugBg(info?.slug) === want && info?.prospectId) {
      return Number(info.prospectId)
    }
  }
  return null
}

function degreeLabelFromProbe(payload) {
  const d = Number(payload?.degree)
  if (d === 1) return '1º (contacto)'
  if (d === 2) return '2º (no contacto)'
  if (d === 3) return '3º (no contacto)'
  const v = String(payload?.verdict || payload?.connectionStatus || '').toLowerCase()
  if (v === 'connected') return '1º (contacto)'
  if (v === 'not_connected') return '2º/3º (no contacto)'
  return null
}

function summarizeProbeDiag(payload) {
  if (payload?.phase === 'opening' || payload?.phase === 'reading') {
    return payload.summary || 'Leyendo grado en LinkedIn…'
  }
  const name = String(payload?.prospectName || '').trim()
  const who = name ? ` · ${name}` : payload?.prospectId ? ` · #${payload.prospectId}` : ''
  const via = payload?.via ? ` vía ${payload.via}` : ''
  const label = degreeLabelFromProbe(payload)
  const readOk =
    payload?.readOk === true ||
    (payload?.ok &&
      (label ||
        payload?.verdict === 'connected' ||
        payload?.verdict === 'not_connected' ||
        payload?.reported))
  if (readOk && label) {
    return `SÍ leyó${who}: ${label}${via}`
  }
  if (readOk && payload?.verdict) {
    return `SÍ leyó${who}: ${payload.verdict}${via}`
  }
  const err = String(payload?.error || payload?.reason || 'sin grado').trim()
  return `NO leyó${who}: ${err}${via}`
}

async function saveLastProbeDiag(payload) {
  const enriched = {
    ...payload,
    degreeLabel: degreeLabelFromProbe(payload) || payload?.degreeLabel || null,
    readOk:
      payload?.readOk === true ||
      Boolean(
        payload?.ok &&
          (payload?.verdict === 'connected' ||
            payload?.verdict === 'not_connected' ||
            Number(payload?.degree) === 1 ||
            Number(payload?.degree) === 2 ||
            Number(payload?.degree) === 3),
      ),
    summary: payload?.summary || summarizeProbeDiag(payload),
    at: Date.now(),
  }
  try {
    await chrome.storage.local.set({ nexusLastProbeDiag: enriched })
  } catch {
    /* ignore */
  }
  try {
    notifyNexusTabs({ type: 'NEXUS_LINKEDIN_PROBE_DIAG', ...enriched })
  } catch {
    /* ignore */
  }
}

/** Solo nuestras pestañas de verificación llevan ?nexus_probe=… */
function isNexusProbeTabUrl(url) {
  try {
    const u = new URL(String(url || ''))
    if (!u.hostname.includes('linkedin.com')) return false
    return u.searchParams.has('nexus_probe')
  } catch {
    return /[?&]nexus_probe=/i.test(String(url || ''))
  }
}

function withNexusProbeParam(profileUrl, prospectId) {
  try {
    const u = new URL(profileUrl)
    u.searchParams.set('nexus_probe', String(prospectId || Date.now()))
    return u.toString()
  } catch {
    return profileUrl
  }
}

/**
 * Cierra SOLO si es pestaña de probe Nexus (?nexus_probe=).
 * Si el id fue reutilizado por Chrome en tu LinkedIn manual → NO la toca.
 */
async function safeCloseProbeTab(tabId) {
  const id = Number(tabId || 0)
  if (!id) return
  let url = ''
  try {
    const tab = await chrome.tabs.get(id)
    url = tab?.url || ''
  } catch {
    await forgetProbeTab(id)
    return
  }
  if (!isNexusProbeTabUrl(url) && !nexusOwnedProbeTabs.has(id)) {
    // Ni param ni id nuestro → pestaña del usuario.
    await forgetProbeTab(id)
    return
  }
  await closeOurCreatedProbeTab(id)
}

/**
 * Cierra la pestaña que NOSOTROS creamos para el sondeo (por tabId).
 * LinkedIn a menudo saca ?nexus_probe= de la URL → no podemos confiar solo en eso.
 */
async function closeOurCreatedProbeTab(tabId) {
  const id = Number(tabId || 0)
  if (!id) return
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      await chrome.tabs.remove(id)
      await forgetProbeTab(id)
      return
    } catch {
      await sleep(120)
    }
  }
  await forgetProbeTab(id)
}

async function closeAllOwnedProbeTabs() {
  try {
    const stored = await chrome.storage.local.get([PROBE_TABS_KEY, PROBE_META_KEY])
    const meta = stored?.[PROBE_META_KEY] || {}
    for (const id of stored?.[PROBE_TABS_KEY] || []) {
      const n = Number(id)
      if (!n) continue
      const info = meta[String(n)] || {}
      if (!nexusOwnedProbeTabs.has(n)) {
        nexusOwnedProbeTabs.set(n, {
          prospectId: Number(info.prospectId || 0) || 0,
          slug: String(info.slug || ''),
        })
      }
    }
  } catch {
    /* ignore */
  }
  const ids = [...nexusOwnedProbeTabs.keys()]
  for (const id of ids) {
    await closeOurCreatedProbeTab(id)
  }
  try {
    await chrome.storage.local.set({ [PROBE_TABS_KEY]: [], [PROBE_META_KEY]: {} })
  } catch {
    /* ignore */
  }
}

const PROBE_ATTEMPT_KEY = 'nexusLiProbeAttempts'
/** Entre visitas: ~12s para varias lecturas reales dentro de la ventana de 120s. */
const PROBE_COOLDOWN_MS = 8 * 1000
const PROBE_MAX_ATTEMPTS_KEY = 'nexusLiProbeAttemptCounts'
const PROBE_BURST_MAX = 8
const PROBE_BURST_RESET_MS = 3 * 60 * 1000
/** Reintento rápido mientras haya checking (alarm Chrome mínimo = 1 min). */
const PROBE_FOLLOWUP_MS = 15 * 1000
const PROBE_FOLLOWUP_MAX = 10
let probeFollowupTimer = null
let probeFollowupLeft = 0
/** Prospectos ya leídos (1/2/3): no volver a abrir pestaña. */
const PROBE_RESOLVED_KEY = 'nexusLiProbeResolved'

function scheduleProbeFollowup(hasCheckingWork) {
  if (!hasCheckingWork) {
    probeFollowupLeft = 0
    if (probeFollowupTimer) {
      clearTimeout(probeFollowupTimer)
      probeFollowupTimer = null
    }
    return
  }
  if (probeFollowupLeft <= 0) probeFollowupLeft = PROBE_FOLLOWUP_MAX
  if (probeFollowupTimer) return
  probeFollowupTimer = setTimeout(() => {
    probeFollowupTimer = null
    probeFollowupLeft = Math.max(0, probeFollowupLeft - 1)
    void probePendingLinkedInConnections()
  }, PROBE_FOLLOWUP_MS)
}

async function wasProbeResolved(prospectId) {
  const id = String(prospectId || '')
  if (!id) return false
  try {
    const stored = await chrome.storage.local.get(PROBE_RESOLVED_KEY)
    return Boolean(stored?.[PROBE_RESOLVED_KEY]?.[id])
  } catch {
    return false
  }
}

async function markProbeResolved(prospectId, degree) {
  const id = String(prospectId || '')
  if (!id) return
  try {
    const stored = await chrome.storage.local.get(PROBE_RESOLVED_KEY)
    const map = { ...(stored?.[PROBE_RESOLVED_KEY] || {}) }
    map[id] = { degree, at: Date.now() }
    // Limpiar entradas viejas (>2 días)
    const cutoff = Date.now() - 2 * 24 * 60 * 60 * 1000
    for (const k of Object.keys(map)) {
      if (Number(map[k]?.at || 0) < cutoff) delete map[k]
    }
    await chrome.storage.local.set({ [PROBE_RESOLVED_KEY]: map })
  } catch {
    /* ignore */
  }
}

async function clearProbeResolved(prospectId) {
  const id = String(prospectId || '')
  if (!id) return
  try {
    const stored = await chrome.storage.local.get(PROBE_RESOLVED_KEY)
    const map = { ...(stored?.[PROBE_RESOLVED_KEY] || {}) }
    if (!(id in map)) return
    delete map[id]
    await chrome.storage.local.set({ [PROBE_RESOLVED_KEY]: map })
  } catch {
    /* ignore */
  }
}

/** Ya abrimos pestaña de verify para este prospecto (aunque aún esté leyendo). */
const PROBE_TAB_OPENED_KEY = 'nexusLiProbeTabOpened'
const PROBE_TAB_OPENED_TTL_MS = 3 * 60 * 1000

async function wasProbeTabOpened(prospectId) {
  const id = String(prospectId || '')
  if (!id) return false
  try {
    const stored = await chrome.storage.local.get(PROBE_TAB_OPENED_KEY)
    const at = Number(stored?.[PROBE_TAB_OPENED_KEY]?.[id] || 0)
    return Boolean(at && Date.now() - at < PROBE_TAB_OPENED_TTL_MS)
  } catch {
    return false
  }
}

async function getProbeTabOpenedAt(prospectId) {
  const id = String(prospectId || '')
  if (!id) return 0
  try {
    const stored = await chrome.storage.local.get(PROBE_TAB_OPENED_KEY)
    return Number(stored?.[PROBE_TAB_OPENED_KEY]?.[id] || 0)
  } catch {
    return 0
  }
}

async function clearProbeTabOpened(prospectId) {
  const id = String(prospectId || '')
  if (!id) return
  try {
    const stored = await chrome.storage.local.get(PROBE_TAB_OPENED_KEY)
    const map = { ...(stored?.[PROBE_TAB_OPENED_KEY] || {}) }
    delete map[id]
    await chrome.storage.local.set({ [PROBE_TAB_OPENED_KEY]: map })
  } catch {
    /* ignore */
  }
}

async function markProbeTabOpened(prospectId) {
  const id = String(prospectId || '')
  if (!id) return
  try {
    const stored = await chrome.storage.local.get(PROBE_TAB_OPENED_KEY)
    const map = { ...(stored?.[PROBE_TAB_OPENED_KEY] || {}) }
    map[id] = Date.now()
    const cutoff = Date.now() - PROBE_TAB_OPENED_TTL_MS * 5
    for (const k of Object.keys(map)) {
      if (Number(map[k] || 0) < cutoff) delete map[k]
    }
    await chrome.storage.local.set({ [PROBE_TAB_OPENED_KEY]: map })
  } catch {
    /* ignore */
  }
}

async function wasProbedRecently(prospectId) {
  const id = String(prospectId || '')
  if (!id) return false
  try {
    const stored = await chrome.storage.local.get(PROBE_ATTEMPT_KEY)
    const map = stored?.[PROBE_ATTEMPT_KEY] || {}
    const at = Number(map[id] || 0)
    return Boolean(at && Date.now() - at < PROBE_COOLDOWN_MS)
  } catch {
    return false
  }
}

async function probeAttemptCount(prospectId) {
  const id = String(prospectId || '')
  if (!id) return 0
  try {
    const stored = await chrome.storage.local.get([PROBE_ATTEMPT_KEY, PROBE_MAX_ATTEMPTS_KEY])
    const map = stored?.[PROBE_ATTEMPT_KEY] || {}
    const counts = stored?.[PROBE_MAX_ATTEMPTS_KEY] || {}
    const at = Number(map[id] || 0)
    const n = Number(counts[id] || 0)
    // Sin timestamp (p.ej. se limpió cooldown) → no bloquear por contador viejo.
    if (!at) return 0
    // Tras pausa, reiniciar contador: no dejar prospectos eternamente sin sondear.
    if (Date.now() - at >= PROBE_BURST_RESET_MS) return 0
    return n
  } catch {
    return 0
  }
}

async function markProbedAttempt(prospectId) {
  const id = String(prospectId || '')
  if (!id) return
  try {
    const stored = await chrome.storage.local.get([PROBE_ATTEMPT_KEY, PROBE_MAX_ATTEMPTS_KEY])
    const map = { ...(stored?.[PROBE_ATTEMPT_KEY] || {}) }
    const counts = { ...(stored?.[PROBE_MAX_ATTEMPTS_KEY] || {}) }
    const prevAt = Number(map[id] || 0)
    const prevN = Number(counts[id] || 0)
    const reset = !prevAt || Date.now() - prevAt >= PROBE_BURST_RESET_MS
    map[id] = Date.now()
    counts[id] = reset ? 1 : prevN + 1
    const cutoff = Date.now() - PROBE_BURST_RESET_MS * 10
    for (const k of Object.keys(map)) {
      if (Number(map[k] || 0) < cutoff) {
        delete map[k]
        delete counts[k]
      }
    }
    await chrome.storage.local.set({
      [PROBE_ATTEMPT_KEY]: map,
      [PROBE_MAX_ATTEMPTS_KEY]: counts,
    })
  } catch {
    /* ignore */
  }
}

/**
 * Fallback MAIN-world: Voyager dash/profiles primero, luego badge, luego networkinfo.
 * @returns {Promise<'connected'|'not_connected'|null>}
 */
async function detectConnectionPageFn(_rawSlug) {
  const slug = String(_rawSlug || '').trim()
  if (!slug) return null

  const toVerdict = (raw) => {
    if (raw == null) return null
    if (typeof raw === 'number') {
      if (raw === 1) return 'connected'
      if (raw === 2 || raw === 3) return 'not_connected'
      return null
    }
    const t = String(raw).toUpperCase()
    if (t.includes('DISTANCE_1') || t === '1') return 'connected'
    if (
      t.includes('DISTANCE_2') ||
      t.includes('DISTANCE_3') ||
      t.includes('OUT_OF_NETWORK') ||
      t === '2' ||
      t === '3'
    ) {
      return 'not_connected'
    }
    return null
  }

  const identities = new Set([slug])
  try {
    identities.add(decodeURIComponent(slug))
  } catch {
    /* ignore */
  }
  try {
    const d = decodeURIComponent(slug)
    if (typeof d.normalize === 'function') {
      identities.add(d.normalize('NFC'))
      identities.add(d.normalize('NFD').replace(/\p{M}/gu, ''))
      // No encodeURIComponent acá: se vuelve a encodear en la URL de Voyager.
    }
  } catch {
    /* ignore */
  }

  const cookie = document.cookie
    .split(';')
    .map((s) => s.trim())
    .find((s) => s.startsWith('JSESSIONID='))
  let csrf = ''
  if (cookie) {
    csrf = cookie.slice('JSESSIONID='.length)
    try {
      csrf = decodeURIComponent(csrf).replace(/"/g, '')
    } catch {
      csrf = csrf.replace(/"/g, '')
    }
  }

  const headers = {
    accept: 'application/vnd.linkedin.normalized+json+2.1',
    'csrf-token': csrf,
    'x-restli-protocol-version': '2.0.0',
    'x-li-lang': document.documentElement?.lang || 'es_ES',
  }

  const decorations = [
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-128',
    'com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-16',
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardCore-16',
  ]

  if (csrf) {
    for (const identity of identities) {
      for (const decorationId of decorations) {
        try {
          const u =
            `https://www.linkedin.com/voyager/api/identity/dash/profiles` +
            `?q=memberIdentity&memberIdentity=${encodeURIComponent(identity)}` +
            `&decorationId=${encodeURIComponent(decorationId)}`
          const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
          if (!res.ok) continue
          const data = await res.json()
          const entities = []
          if (Array.isArray(data?.included)) entities.push(...data.included)
          if (data?.data) {
            if (Array.isArray(data.data)) entities.push(...data.data)
            else entities.push(data.data)
          }
          const want = String(identity).toLowerCase()
          const strip = (s) => {
            try {
              return String(s || '')
                .normalize('NFD')
                .replace(/\p{M}/gu, '')
                .toLowerCase()
            } catch {
              return String(s || '').toLowerCase()
            }
          }
          for (const ent of entities) {
            if (!ent || typeof ent !== 'object') continue
            const pub = String(ent.publicIdentifier || '').toLowerCase()
            if (!pub) continue
            if (pub !== want && strip(pub) !== strip(want)) continue
            const raw =
              ent.memberDistance?.value ||
              ent.memberDistance ||
              ent.networkDistance ||
              ent.distance
            const v = toVerdict(raw)
            if (v) return v
          }
          const primary = data?.data
          if (primary && typeof primary === 'object' && !Array.isArray(primary)) {
            const raw =
              primary.memberDistance?.value ||
              primary.memberDistance ||
              primary.networkDistance ||
              primary.distance
            const v = toVerdict(raw)
            if (v) return v
          }
        } catch {
          /* next */
        }
      }
    }

    for (const identity of identities) {
      try {
        const u = `https://www.linkedin.com/voyager/api/identity/profiles/${encodeURIComponent(identity)}/networkinfo`
        const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
        if (!res.ok) continue
        const data = await res.json()
        const dist = data?.data?.distance || data?.distance
        const v = toVerdict(dist?.value ?? dist)
        if (v) return v
      } catch {
        /* next */
      }
    }
  }

  const degreeFromText = (raw, { shortOnly = false } = {}) => {
    const t = String(raw || '')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase()
    if (!t) return null
    if (shortOnly && t.length > 12) return null
    if (t.length > 48) return null
    // Sin \b tras º/° (rompe "2º" / "· 2º").
    if (/^(?:·|•)?\s*1(?:er|º|°|st|ro)?$/.test(t)) return 1
    if (/^(?:·|•)?\s*2(?:º|°|nd|do)?$/.test(t)) return 2
    if (/^(?:·|•)?\s*3(?:er|º|°|rd|ro)?$/.test(t)) return 3
    if (/[·•]\s*1(?:er|º|°|st|ro)?/i.test(t) || /\b1(?:er|st|ro)\b/i.test(t) || /\b1[º°]/i.test(t))
      return 1
    if (/[·•]\s*2(?:º|°|nd|do)?/i.test(t) || /\b2(?:nd|do)\b/i.test(t) || /\b2[º°]/i.test(t))
      return 2
    if (/[·•]\s*3(?:er|º|°|rd|ro)?/i.test(t) || /\b3(?:rd|er|ro)\b/i.test(t) || /\b3[º°]/i.test(t))
      return 3
    return null
  }

  for (let i = 0; i < 10; i++) {
    for (const sel of [
      '.dist-value',
      '.distance-badge .dist-value',
      '.distance-badge',
      'span.artdeco-entity-lockup__degree',
    ]) {
      for (const el of document.querySelectorAll(sel)) {
        const d = degreeFromText((el.textContent || '').replace(/\s+/g, ' ').trim(), {
          shortOnly: true,
        })
        if (d === 1) return 'connected'
        if (d === 2 || d === 3) return 'not_connected'
      }
    }
    await new Promise((r) => setTimeout(r, 200))
  }
  return null
}

async function askTabForVerdict(tabId, slug, { fast = true, inviteMode = false } = {}) {
  const id = Number(tabId || 0)
  if (!id || !slug) return null

  const pingTries = fast ? 4 : 10
  const pingDelay = fast ? 180 : 350
  for (let i = 0; i < pingTries; i++) {
    try {
      const ping = await chrome.tabs.sendMessage(id, { type: 'NEXUS_PING_CONNECT' })
      if (ping?.ok) break
    } catch {
      if (i === 0 || i === 2) {
        try {
          await chrome.scripting.executeScript({
            target: { tabId: id },
            files: ['content-linkedin-connect.js'],
          })
        } catch {
          /* ignore */
        }
      }
    }
    await sleep(pingDelay)
  }

  try {
    const res = await chrome.tabs.sendMessage(id, {
      type: 'NEXUS_READ_CONNECTION_VERDICT',
      profileSlug: slug,
      fast: Boolean(fast),
      mode: inviteMode ? 'invite_sent' : undefined,
    })
    const v = res?.verdict
    if (v === 'connected' || v === 'not_connected') return v
    if (inviteMode && v === 'not_yet') return 'not_yet'
  } catch {
    /* ignore */
  }

  try {
    const res = await chrome.tabs.sendMessage(id, {
      type: 'NEXUS_VOYAGER_DISTANCE',
      profileSlug: slug,
    })
    const v = res?.verdict
    if (v === 'connected' || v === 'not_connected') return v
  } catch {
    /* ignore */
  }

  return injectDetectConnection(id, slug)
}

async function injectDetectConnection(tabId, slug) {
  const id = Number(tabId || 0)
  if (!id) return null
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId: id },
      world: 'MAIN',
      func: detectConnectionPageFn,
      args: [String(slug || '')],
    })
    const result = injected?.[0]?.result
    if (result === 'connected' || result === 'not_connected') return result
  } catch {
    /* ignore */
  }
  return null
}

async function injectVoyagerDistance(tabId, slug) {
  return askTabForVerdict(tabId, slug, { fast: true })
}

/**
 * Preferir pestaña LinkedIn ya abierta (incluye las de probe de Nexus).
 * Si ya está EN el perfil → activar un instante y leer badge (LinkedIn no pinta ·1er inactivo).
 * Si no → Voyager desde cualquier tab LinkedIn.
 * @returns {Promise<'connected'|'not_connected'|null>}
 */
async function readDistanceFromExistingLinkedInTab(slug, { includeOwned = true } = {}) {
  if (!slug) return null
  const needles = slugMatchNeedles(slug)
  try {
    const tabs = await chrome.tabs.query({ url: ['https://www.linkedin.com/*'] })
    // 1) Tabs ya en ese perfil: el badge ·1er / ·2º es lo más confiable.
    for (const tab of tabs || []) {
      if (!tab?.id) continue
      if (!includeOwned && nexusOwnedProbeTabs.has(tab.id)) continue
      const tabUrl = String(tab.url || '').toLowerCase()
      if (!tabUrl.includes('/in/')) continue
      if (!needles.some((n) => n && tabUrl.includes(n))) continue
      try {
        // NO activar pestaña: no robar foco de Nexus.
        try {
          const injected = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            world: 'MAIN',
            func: debugReadAndShowDegreeFn,
            args: [String(slug || '')],
          })
          const n = injected?.[0]?.result
          if (n === 1) return 'connected'
          if (n === 2 || n === 3) return 'not_connected'
        } catch {
          /* fall through */
        }
        await ensureConnectContentScript(tab.id)
        const v = await askTabForVerdict(tab.id, slug, { fast: true })
        if (v === 'connected' || v === 'not_connected') return v
      } catch {
        /* next */
      }
    }
    // 2) Voyager desde cualquier LinkedIn abierto.
    for (const tab of tabs || []) {
      if (!tab?.id) continue
      if (!includeOwned && nexusOwnedProbeTabs.has(tab.id)) continue
      try {
        await ensureConnectContentScript(tab.id)
        const res = await chrome.tabs.sendMessage(tab.id, {
          type: 'NEXUS_VOYAGER_DISTANCE',
          profileSlug: slug,
        })
        const v = res?.verdict
        if (v === 'connected' || v === 'not_connected') return v
      } catch {
        /* tab sin content script */
      }
      try {
        const injected = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: 'MAIN',
          func: detectConnectionPageFn,
          args: [String(slug || '')],
        })
        const result = injected?.[0]?.result
        if (result === 'connected' || result === 'not_connected') return result
      } catch {
        /* next */
      }
    }
  } catch {
    /* ignore */
  }
  return null
}

/** Variantes de slug para matchear URL encoded / con acentos. */
function slugMatchNeedles(slug) {
  const raw = String(slug || '').trim()
  const out = new Set()
  const add = (s) => {
    const v = String(s || '').trim().toLowerCase()
    if (v) out.add(v)
  }
  add(raw)
  try {
    add(decodeURIComponent(raw))
  } catch {
    /* ignore */
  }
  try {
    add(encodeURIComponent(raw))
  } catch {
    /* ignore */
  }
  try {
    const d = decodeURIComponent(raw)
    if (typeof d.normalize === 'function') {
      add(d.normalize('NFD').replace(/\p{M}/gu, ''))
    }
  } catch {
    /* ignore */
  }
  return [...out]
}

async function ensureConnectContentScript(tabId) {
  const id = Number(tabId || 0)
  if (!id) return
  try {
    const ping = await chrome.tabs.sendMessage(id, { type: 'NEXUS_PING_CONNECT' })
    if (ping?.ok) return
  } catch {
    /* inject */
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId: id },
      files: ['content-linkedin-connect.js'],
    })
    await sleep(200)
  } catch {
    /* ignore */
  }
}

/**
 * Lista pending → abrir cada perfil, leer badge, cerrar, reportar.
 */
async function probePendingLinkedInConnections() {
  if (LI_SAFE_NO_PROFILE_PROBE) {
    return { ok: true, skipped: true, reason: 'li_safe_no_probe', results: [] }
  }
  return withProbeLock(async () => {
    const auth = await getAuth()
    if (!auth?.token || !auth.companyId) {
      scheduleProbeFollowup(false)
      return { ok: false, error: 'no_auth', results: [] }
    }
    // NO cerrar pestañas acá: closeAllOwnedProbeTabs llegaba a matar el LinkedIn del usuario.
    const results = []
    try {
      const res = await fetch(
        `${auth.apiBaseUrl}/companies/${auth.companyId}/linkedin-assisted/pending-connect-checks`,
        {
          headers: { Authorization: `Bearer ${auth.token}` },
        },
      )
      if (!res.ok) {
        scheduleProbeFollowup(false)
        return { ok: false, error: `api_${res.status}`, results }
      }
      const data = await res.json().catch(() => ({}))
      const items = Array.isArray(data?.items) ? data.items : []
      const checking = items
        .filter((i) => String(i?.connection_status || '') === 'checking')
        .slice(0, 1)
      if (!checking.length) {
        scheduleProbeFollowup(false)
        return { ok: true, results, empty: true }
      }
      for (const item of checking) {
        const pid = Number(item?.prospect_id || 0)
        // Backend todavía pide verify → no bloquear por un "resolved" falso viejo.
        if (pid) await clearProbeResolved(pid)
        // tab_already_opened / cooldown: probeOneConnection relee o reabre solo.
        // NO skipear acá: si no leyó el grado, hay que reintentar automáticamente.
        if (pid && (await wasProbedRecently(pid)) && !(await wasProbeTabOpened(pid))) {
          results.push({ prospectId: pid, skipped: true, reason: 'cooldown' })
          continue
        }
        const one = await probeOneConnection(item, { allowCreateTab: true })
        results.push({
          prospectId: pid,
          ok: Boolean(one?.ok),
          verdict: one?.verdict || null,
          degree: one?.degree ?? null,
          error: one?.error || null,
          skipped: Boolean(one?.skipped),
          reason: one?.reason || null,
        })
        await sleep(600)
      }
      // Seguir si quedó trabajo (leyendo / falló / cooldown corto).
      const stillWork = results.some(
        (r) =>
          (r.skipped && (r.reason === 'cooldown' || r.reason === 'tab_reading')) ||
          (!r.ok && !r.skipped),
      )
      scheduleProbeFollowup(stillWork)
      return { ok: true, results }
    } catch (err) {
      scheduleProbeFollowup(true)
      return { ok: false, error: String(err?.message || err), results }
    }
  })
}

/**
 * Lectura del grado: Voyager → badge ·1er/·2º/·3º → CTA Contactar.
 * Importante: en ES el badge es "· 2º" / "· 3º"; NO usar \b después de º (falla).
 * Solo muestra overlay cuando SÍ leyó (nunca "NO LEÍ").
 * @returns {Promise<1|2|3|null>}
 */
async function debugReadAndShowDegreeFn(_rawSlug) {
  const slug = String(_rawSlug || '').trim()

  const show = (degree, detail) => {
    if (!(degree === 1 || degree === 2 || degree === 3)) return
    try {
      const old = document.getElementById('nexus-degree-debug')
      if (old) old.remove()
      const el = document.createElement('div')
      el.id = 'nexus-degree-debug'
      const label = degree === 1 ? '1 (contacto)' : String(degree)
      el.textContent = detail ? `Nexus lee: ${label} · ${detail}` : `Nexus lee: ${label}`
      el.style.cssText =
        'position:fixed;z-index:2147483647;top:20px;left:50%;transform:translateX(-50%);' +
        'background:#0A66C2;color:#fff;padding:16px 24px;border-radius:12px;' +
        'font:700 18px/1.35 system-ui,sans-serif;box-shadow:0 10px 28px rgba(0,0,0,.4);' +
        'max-width:94vw;text-align:center;white-space:pre-wrap'
      ;(document.documentElement || document.body).appendChild(el)
      window.setTimeout(() => {
        try {
          el.remove()
        } catch {
          /* ignore */
        }
      }, 3500)
    } catch {
      /* ignore */
    }
  }

  // º/° NO son word-chars: \b después de ellos rompe "· 2º" / "· 3º" / "1º".
  const hasFirst = (t) =>
    /[·•]\s*1(?:[\s.]*(?:er|ero|º|°|st|ro))?/i.test(t) ||
    /\b1(?:ero|er|st|ro)\b/i.test(t) ||
    /\b1[º°]/i.test(t) ||
    /\b1st\b/i.test(t) ||
    /\b1(?:er)?\s*grado\b/i.test(t) ||
    /\bfirst[-\s]?degree\b/i.test(t)

  const hasSecond = (t) =>
    /[·•]\s*2(?:[\s.]*(?:º|°|nd|do))?/i.test(t) ||
    /\b2(?:do|nd)\b/i.test(t) ||
    /\b2[º°]/i.test(t) ||
    /\b2nd\b/i.test(t) ||
    /\b2(?:do)?\s*grado\b/i.test(t) ||
    /\bsecond[-\s]?degree\b/i.test(t)

  const hasThird = (t) =>
    /[·•]\s*3(?:[\s.]*(?:er|ero|º|°|rd|ro))?/i.test(t) ||
    /\b3(?:ero|er|ro|rd)\b/i.test(t) ||
    /\b3[º°]/i.test(t) ||
    /\b3rd\b/i.test(t) ||
    /\b3(?:er|ro)?\s*grado\b/i.test(t) ||
    /\bthird[-\s]?degree\b/i.test(t)

  /** Badge corto: 1/2/3 ok. Texto amplio: 2/3 ganan; 1º solo con ·1er (no “1er” suelto). */
  const findDegree = (raw, { shortBadge = false } = {}) => {
    const t = String(raw || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    if (!t) return null
    if (hasSecond(t)) return 2
    if (hasThird(t)) return 3
    if (shortBadge && hasFirst(t)) return 1
    // 1º estricto: marcador de grado junto al nombre (· 1er), no “1er puesto” / mutuals.
    if (/[·•]\s*1(?:[\s.]*(?:er|ero|º|°|st|ro))?/i.test(t) || /\b1(?:er)?\s*grado\b/i.test(t)) {
      return 1
    }
    return null
  }

  const nearNameSnippet = () => {
    const h1 =
      document.querySelector('main h1') ||
      document.querySelector('h1') ||
      document.querySelector('[data-anonymize="person-name"]')
    if (!h1) return ''
    const box =
      h1.closest('section') ||
      h1.closest('[data-view-name="profile-top-card"]') ||
      h1.closest('.pv-top-card') ||
      h1.parentElement?.parentElement?.parentElement ||
      h1.parentElement
    const parts = [
      h1.textContent || '',
      h1.parentElement?.innerText || '',
      box?.innerText || '',
    ]
    return parts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 600)
  }

  const scanDistanceBadges = () => {
    for (const sel of [
      '.dist-value',
      '.distance-badge .dist-value',
      '.distance-badge',
      'span.artdeco-entity-lockup__degree',
      '[class*="distance-badge"]',
      '[class*="DistanceBadge"]',
      '[class*="dist-value"]',
    ]) {
      for (const el of document.querySelectorAll(sel)) {
        const raw = (el.textContent || '').replace(/\s+/g, ' ').trim()
        if (!raw || raw.length > 24) continue
        const d = findDegree(raw, { shortBadge: true })
        if (d) return { degree: d, via: 'badge:' + raw }
      }
    }
    return null
  }

  const scanCtas = () => {
    const roots = [
      ...document.querySelectorAll(
        [
          '.pvs-profile-actions',
          '.pv-top-card-v2-ctas',
          '[data-view-name="profile-top-card"]',
          'main .pv-top-card',
          'main section.artdeco-card',
          '.ph5.pb5',
        ].join(', '),
      ),
    ]
    const scope = roots.length ? roots : [document.querySelector('main')].filter(Boolean)
    let connectBtn = false
    let pendingBtn = false
    for (const root of scope) {
      for (const el of root.querySelectorAll(
        'button, a[role="button"], div[role="button"], a.artdeco-button',
      )) {
        const label = `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
          .toLowerCase()
          .replace(/\s+/g, ' ')
          .trim()
        if (!label || label.length > 80) continue
        if (/\b(pending|pendiente)\b/.test(label)) pendingBtn = true
        if (
          /^(contactar|conectar|connect)\b/.test(label) ||
          /\binvitar a conectar\b/.test(label) ||
          /\binvite\b/.test(label)
        ) {
          connectBtn = true
        }
      }
    }
    if (
      document.querySelector(
        'a[href*="/preload/custom-invite"], a[href*="custom-invite/?vanityName"], a[href*="custom-invite?"]',
      )
    ) {
      connectBtn = true
    }
    if (pendingBtn) return { degree: 2, via: 'btn-pendiente' }
    if (connectBtn) return { degree: 2, via: 'btn-contactar' }
    return null
  }

  const scanSimple = () => {
    // Contactar / Pendiente siempre veta “contacto”.
    const cta = scanCtas()
    if (cta) return cta
    const badge = scanDistanceBadges()
    if (badge) return badge
    const near = nearNameSnippet()
    const nearDeg = findDegree(near, { shortBadge: false })
    if (nearDeg === 2 || nearDeg === 3) return { degree: nearDeg, via: 'nombre' }
    if (nearDeg === 1) return { degree: 1, via: 'nombre-estricto' }
    return { degree: null, via: near.slice(0, 90) || 'sin-texto' }
  }

  const voyagerDegree = async () => {
    if (!slug) return null
    const toNum = (raw) => {
      if (raw == null) return null
      if (typeof raw === 'number') {
        if (raw === 1 || raw === 2 || raw === 3) return raw
        return null
      }
      const t = String(raw).toUpperCase()
      if (t.includes('DISTANCE_1') || t === '1') return 1
      if (t.includes('DISTANCE_2') || t === '2') return 2
      if (t.includes('DISTANCE_3') || t.includes('OUT_OF_NETWORK') || t === '3') return 3
      return null
    }
    const identities = new Set([slug])
    try {
      identities.add(decodeURIComponent(slug))
    } catch {
      /* ignore */
    }
    try {
      const d = decodeURIComponent(slug)
      if (typeof d.normalize === 'function') {
        identities.add(d.normalize('NFC'))
        identities.add(d.normalize('NFD').replace(/\p{M}/gu, ''))
      }
    } catch {
      /* ignore */
    }
    const cookie = document.cookie
      .split(';')
      .map((s) => s.trim())
      .find((s) => s.startsWith('JSESSIONID='))
    let csrf = ''
    if (cookie) {
      csrf = cookie.slice('JSESSIONID='.length)
      try {
        csrf = decodeURIComponent(csrf).replace(/"/g, '')
      } catch {
        csrf = csrf.replace(/"/g, '')
      }
    }
    if (!csrf) return null
    const headers = {
      accept: 'application/vnd.linkedin.normalized+json+2.1',
      'csrf-token': csrf,
      'x-restli-protocol-version': '2.0.0',
      'x-li-lang': document.documentElement?.lang || 'es_ES',
    }
    const decorations = [
      'com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-128',
      'com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-16',
      'com.linkedin.voyager.dash.deco.identity.profile.TopCardCore-16',
    ]
    const strip = (s) => {
      try {
        return String(s || '')
          .normalize('NFD')
          .replace(/\p{M}/gu, '')
          .toLowerCase()
      } catch {
        return String(s || '').toLowerCase()
      }
    }
    for (const identity of identities) {
      for (const decorationId of decorations) {
        try {
          const u =
            `https://www.linkedin.com/voyager/api/identity/dash/profiles` +
            `?q=memberIdentity&memberIdentity=${encodeURIComponent(identity)}` +
            `&decorationId=${encodeURIComponent(decorationId)}`
          const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
          if (!res.ok) continue
          const data = await res.json()
          const entities = []
          if (Array.isArray(data?.included)) entities.push(...data.included)
          if (data?.data) {
            if (Array.isArray(data.data)) entities.push(...data.data)
            else entities.push(data.data)
          }
          const want = String(identity).toLowerCase()
          for (const ent of entities) {
            if (!ent || typeof ent !== 'object') continue
            const pub = String(ent.publicIdentifier || '').toLowerCase()
            if (!pub) continue
            if (pub !== want && strip(pub) !== strip(want)) continue
            const n = toNum(
              ent.memberDistance?.value ||
                ent.memberDistance ||
                ent.networkDistance ||
                ent.distance,
            )
            if (n) return n
          }
          const primary = data?.data
          if (primary && typeof primary === 'object' && !Array.isArray(primary)) {
            const n = toNum(
              primary.memberDistance?.value ||
                primary.memberDistance ||
                primary.networkDistance ||
                primary.distance,
            )
            if (n) return n
          }
        } catch {
          /* next */
        }
      }
    }
    for (const identity of identities) {
      try {
        const u = `https://www.linkedin.com/voyager/api/identity/profiles/${encodeURIComponent(identity)}/networkinfo`
        const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
        if (!res.ok) continue
        const data = await res.json()
        const dist = data?.data?.distance || data?.distance
        const n = toNum(dist?.value ?? dist)
        if (n) return n
      } catch {
        /* next */
      }
    }
    return null
  }

  if (/\/login|\/authwall|\/checkpoint/i.test(location.href)) {
    return null
  }

  try {
    window.scrollTo(0, 0)
  } catch {
    /* ignore */
  }

  // Pasada rápida (el SW reintenta afuera tras hydrate).
  try {
    const viaApi = await voyagerDegree()
    if (viaApi === 1 || viaApi === 2 || viaApi === 3) {
      const ctaVeto = scanCtas()
      // Contactar visible gana sobre Voyager 1º (falsos DISTANCE_1 ajenos).
      if (viaApi === 1 && ctaVeto) {
        show(ctaVeto.degree, `${ctaVeto.via}+veto-voyager`)
        return ctaVeto.degree
      }
      show(viaApi, 'voyager')
      return viaApi
    }
  } catch {
    /* fall through */
  }

  for (let i = 0; i < 6; i++) {
    const hit = scanSimple()
    if (hit.degree === 1 || hit.degree === 2 || hit.degree === 3) {
      show(hit.degree, hit.via)
      return hit.degree
    }
    await new Promise((r) => setTimeout(r, 350))
  }

  return null
}

async function getLinkedInCsrfToken() {
  const normalize = (value) => {
    try {
      return decodeURIComponent(String(value || '')).replace(/^"|"$/g, '').trim()
    } catch {
      return String(value || '').replace(/^"|"$/g, '').trim()
    }
  }
  try {
    const c = await chrome.cookies.get({
      url: 'https://www.linkedin.com',
      name: 'JSESSIONID',
    })
    if (c?.value) return normalize(c.value)
  } catch {
    /* fall through */
  }
  try {
    const all = await chrome.cookies.getAll({ name: 'JSESSIONID' })
    for (const c of all || []) {
      if (String(c?.domain || '').includes('linkedin.com') && c?.value) {
        return normalize(c.value)
      }
    }
  } catch {
    /* ignore */
  }
  return ''
}

function voyagerDistanceTokenToVerdict(raw) {
  if (raw == null) return null
  if (typeof raw === 'number') {
    if (raw === 1) return 'connected'
    if (raw === 2 || raw === 3) return 'not_connected'
    return null
  }
  const t = String(raw).toUpperCase()
  if (t.includes('DISTANCE_1') || t === '1' || t === 'SELF') return 'connected'
  if (
    t.includes('DISTANCE_2') ||
    t.includes('DISTANCE_3') ||
    t.includes('OUT_OF_NETWORK') ||
    t === '2' ||
    t === '3'
  ) {
    return 'not_connected'
  }
  return null
}

function extractDistanceFromVoyagerPayload(data, identity) {
  const entities = []
  if (Array.isArray(data?.included)) entities.push(...data.included)
  if (data?.data) {
    if (Array.isArray(data.data)) entities.push(...data.data)
    else entities.push(data.data)
  }
  const strip = (s) => {
    try {
      return String(s || '')
        .normalize('NFD')
        .replace(/\p{M}/gu, '')
        .toLowerCase()
    } catch {
      return String(s || '').toLowerCase()
    }
  }
  const want = String(identity || '').toLowerCase()
  const pick = (ent) =>
    voyagerDistanceTokenToVerdict(
      ent?.memberDistance?.value ||
        ent?.memberDistance ||
        ent?.networkDistance ||
        ent?.distance?.value ||
        ent?.distance,
    )

  for (const ent of entities) {
    if (!ent || typeof ent !== 'object') continue
    const pub = String(ent.publicIdentifier || '').toLowerCase()
    if (pub && want && pub !== want && strip(pub) !== strip(want)) continue
    const v = pick(ent)
    if (v) return v
  }
  // Sin match de identidad: solo aceptar 2º/3º (nunca inventar contacto).
  for (const ent of entities) {
    if (!ent || typeof ent !== 'object') continue
    const v = pick(ent)
    if (v === 'not_connected') return v
  }
  const dist = data?.data?.distance || data?.distance
  const primary = voyagerDistanceTokenToVerdict(dist?.value ?? dist)
  return primary === 'not_connected' ? primary : null
}

/**
 * Fetch Voyager en MAIN world de una pestaña LinkedIn (mismo origen + CSRF del SW).
 * Más fiable que fetch desde el service worker.
 */
async function voyagerDistanceInTab(tabId, slug, csrf) {
  const id = Number(tabId || 0)
  if (!id || !slug || !csrf) return null
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId: id },
      world: 'MAIN',
      args: [String(slug), String(csrf)],
      func: async (rawSlug, csrfToken) => {
        const toVerdict = (raw) => {
          if (raw == null) return null
          if (typeof raw === 'number') {
            if (raw === 1) return 'connected'
            if (raw === 2 || raw === 3) return 'not_connected'
            return null
          }
          const t = String(raw).toUpperCase()
          if (t.includes('DISTANCE_1') || t === '1' || t === 'SELF') return 'connected'
          if (
            t.includes('DISTANCE_2') ||
            t.includes('DISTANCE_3') ||
            t.includes('OUT_OF_NETWORK') ||
            t === '2' ||
            t === '3'
          ) {
            return 'not_connected'
          }
          return null
        }
        const pickFromObj = (obj) => {
          if (!obj || typeof obj !== 'object') return null
          return toVerdict(
            obj.memberDistance?.value ||
              obj.memberDistance ||
              obj.networkDistance ||
              obj.distance?.value ||
              obj.distance ||
              obj?.data?.distance?.value ||
              obj?.data?.distance,
          )
        }
        const identities = [rawSlug]
        try {
          identities.push(decodeURIComponent(rawSlug))
        } catch {
          /* ignore */
        }
        // HTML embebido: solo 2º/3º (DISTANCE_1 en ventana del slug es ruidoso).
        try {
          const html = String(document.documentElement?.innerHTML || '')
          const lower = html.toLowerCase()
          for (const idn of identities) {
            const key = String(idn || '').toLowerCase()
            if (!key || key.length < 3) continue
            const idx = lower.indexOf(key)
            if (idx < 0) continue
            const slice = html.slice(Math.max(0, idx - 800), idx + key.length + 800)
            if (/DISTANCE_2\b|DISTANCE_3\b|OUT_OF_NETWORK/i.test(slice)) return 'not_connected'
          }
        } catch {
          /* ignore */
        }
        const headers = {
          accept: 'application/vnd.linkedin.normalized+json+2.1',
          'csrf-token': csrfToken,
          'x-restli-protocol-version': '2.0.0',
          'x-li-lang': document.documentElement?.lang || 'es_ES',
        }
        const decorations = [
          'com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-128',
          'com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-16',
          'com.linkedin.voyager.dash.deco.identity.profile.TopCardCore-16',
        ]
        const tryUrls = []
        for (const mid of identities) {
          tryUrls.push(
            `https://www.linkedin.com/voyager/api/identity/profiles/${encodeURIComponent(mid)}/networkinfo`,
          )
          tryUrls.push(
            `https://www.linkedin.com/voyager/api/identity/profileView/${encodeURIComponent(mid)}`,
          )
          for (const decorationId of decorations) {
            tryUrls.push(
              `https://www.linkedin.com/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity=${encodeURIComponent(mid)}&decorationId=${encodeURIComponent(decorationId)}`,
            )
          }
        }
        for (const u of tryUrls) {
          try {
            const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
            if (!res.ok) continue
            const data = await res.json()
            // networkinfo / profileView: distance del perfil pedido.
            const early = pickFromObj(data?.data)
            if (early === 'not_connected') return early
            if (
              early === 'connected' &&
              (/\/networkinfo\b/.test(u) || /\/profileView\//.test(u))
            ) {
              return early
            }
            const entities = []
            if (Array.isArray(data?.included)) entities.push(...data.included)
            if (data?.data && typeof data.data === 'object') {
              if (Array.isArray(data.data)) entities.push(...data.data)
              else {
                entities.push(data.data)
                if (Array.isArray(data.data['*elements'])) entities.push(...data.data['*elements'])
                if (Array.isArray(data.data.elements)) entities.push(...data.data.elements)
              }
            }
            const want = String(rawSlug).toLowerCase()
            const strip = (s) => {
              try {
                return String(s || '')
                  .normalize('NFD')
                  .replace(/\p{M}/gu, '')
                  .toLowerCase()
              } catch {
                return String(s || '').toLowerCase()
              }
            }
            const wantStrip = strip(want)
            let matchedIdentity = null
            let unmatched = null
            for (const ent of entities) {
              if (!ent || typeof ent !== 'object') continue
              const pub = String(ent.publicIdentifier || '').toLowerCase()
              const v = pickFromObj(ent)
              if (!v) continue
              if (pub && (pub === want || strip(pub) === wantStrip || pub.includes(want) || want.includes(pub))) {
                matchedIdentity = v
                break
              }
              if (!pub && !unmatched) unmatched = v
            }
            if (matchedIdentity) return matchedIdentity
            // Sin publicIdentifier: aceptar 2º/3º (seguro); no inventar 1º.
            if (unmatched === 'not_connected') return unmatched
            // No escanear DISTANCE_1 global del JSON (falsos contactos).
            const blob = JSON.stringify(data)
            if (/DISTANCE_2|DISTANCE_3|OUT_OF_NETWORK/i.test(blob) && !/DISTANCE_1/i.test(blob)) {
              return 'not_connected'
            }
          } catch {
            /* next url */
          }
        }
        return null
      },
    })
    const v = injected?.[0]?.result
    if (v === 'connected' || v === 'not_connected') return v
  } catch {
    /* ignore */
  }
  return null
}

/** Badge ·1er/·2º + CTA Contactar en el perfil (MAIN). connected solo con evidencia fuerte. */
async function readDegreeOrCtaOnProfile(tabId) {
  const id = Number(tabId || 0)
  if (!id) return null
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId: id },
      world: 'MAIN',
      func: async () => {
        const has1 = (t) =>
          /[·•]\s*1(?:[\s.]*(?:er|ero|º|°|st|ro))?/i.test(t) ||
          /\b1(?:ero|er|st|ro)\b/i.test(t) ||
          /\b1[º°]/i.test(t) ||
          /\b1st\b/i.test(t) ||
          /\b1(?:er)?\s*grado\b/i.test(t) ||
          /\bfirst[-\s]?degree\b/i.test(t)
        const has2 = (t) =>
          /[·•]\s*2(?:[\s.]*(?:º|°|nd|do))?/i.test(t) ||
          /\b2(?:do|nd)\b/i.test(t) ||
          /\b2[º°]/i.test(t) ||
          /\b2nd\b/i.test(t) ||
          /\b2(?:do)?\s*grado\b/i.test(t) ||
          /\bsecond[-\s]?degree\b/i.test(t)
        const has3 = (t) =>
          /[·•]\s*3(?:[\s.]*(?:er|ero|º|°|rd|ro))?/i.test(t) ||
          /\b3(?:ero|er|ro|rd)\b/i.test(t) ||
          /\b3[º°]/i.test(t) ||
          /\b3rd\b/i.test(t) ||
          /\b3(?:er|ro)?\s*grado\b/i.test(t) ||
          /\bthird[-\s]?degree\b/i.test(t)
        const degreeOf = (raw, { shortBadge = false } = {}) => {
          const t = String(raw || '')
            .replace(/\u00a0/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
          if (!t) return null
          if (has2(t)) return 2
          if (has3(t)) return 3
          if (shortBadge && has1(t)) return 1
          if (/[·•]\s*1(?:[\s.]*(?:er|ero|º|°|st|ro))?/i.test(t) || /\b1(?:er)?\s*grado\b/i.test(t)) {
            return 1
          }
          return null
        }
        const readCta = () => {
          for (const el of document.querySelectorAll(
            'main button, main a[role="button"], main a.artdeco-button, .pvs-profile-actions button, .pvs-profile-actions a',
          )) {
            const label = `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
              .toLowerCase()
              .replace(/\s+/g, ' ')
              .trim()
            if (!label || label.length > 90) continue
            if (/\b(pending|pendiente)\b/.test(label)) return { degree: 2, via: 'cta_pendiente' }
            if (
              /^(contactar|conectar|connect)\b/.test(label) ||
              /\binvitar a conectar\b/.test(label)
            ) {
              return { degree: 2, via: 'cta_contactar' }
            }
          }
          if (document.querySelector('a[href*="custom-invite"]')) {
            return { degree: 2, via: 'custom_invite' }
          }
          return null
        }
        for (let i = 0; i < 12; i++) {
          const cta = readCta()
          if (cta) return cta
          for (const sel of [
            '.dist-value',
            '.distance-badge',
            'span.artdeco-entity-lockup__degree',
            '[class*="distance-badge"]',
            '[class*="dist-value"]',
          ]) {
            for (const el of document.querySelectorAll(sel)) {
              const d = degreeOf(el.textContent || '', { shortBadge: true })
              if (d) return { degree: d, via: 'badge' }
            }
          }
          const h1 =
            document.querySelector('main h1') ||
            document.querySelector('h1') ||
            document.querySelector('[data-anonymize="person-name"]')
          if (h1) {
            const box =
              h1.closest('section') ||
              h1.closest('[data-view-name="profile-top-card"]') ||
              h1.parentElement?.parentElement
            const near = `${h1.textContent || ''} ${h1.parentElement?.innerText || ''} ${box?.innerText || ''}`
              .replace(/\s+/g, ' ')
              .slice(0, 700)
            const d = degreeOf(near, { shortBadge: false })
            if (d === 2 || d === 3) return { degree: d, via: 'nombre' }
            if (d === 1) return { degree: 1, via: 'nombre-estricto' }
          }
          await new Promise((r) => setTimeout(r, 250))
        }
        return null
      },
    })
    const hit = injected?.[0]?.result
    if (hit?.degree === 1 || hit?.degree === 2 || hit?.degree === 3) return hit
  } catch {
    /* ignore */
  }
  return null
}

/**
 * Lee 1º/2º/3º: 1) pestaña LinkedIn abierta + CSRF cookie  2) fetch desde SW.
 */
async function voyagerDistanceFromBackground(slug) {
  const identity = String(slug || '').trim()
  if (!identity) return null
  const csrf = await getLinkedInCsrfToken()
  const diag = { slug: identity, csrf: Boolean(csrf), via: null, http: [] }

  if (csrf) {
    try {
      const tabs = await chrome.tabs.query({ url: ['https://www.linkedin.com/*'] })
      for (const tab of tabs || []) {
        if (!tab?.id) continue
        const v = await voyagerDistanceInTab(tab.id, identity, csrf)
        if (v === 'connected' || v === 'not_connected') {
          diag.via = 'tab_main'
          await saveLastProbeDiag({ ...diag, verdict: v, ok: true })
          return v
        }
      }
    } catch {
      /* fall through SW */
    }
  }

  if (!csrf) {
    await saveLastProbeDiag({ ...diag, ok: false, error: 'no_jsessionid_cookie' })
    return null
  }

  const identities = new Set([identity])
  try {
    identities.add(decodeURIComponent(identity))
  } catch {
    /* ignore */
  }
  try {
    const d = decodeURIComponent(identity)
    if (typeof d.normalize === 'function') {
      identities.add(d.normalize('NFC'))
      identities.add(d.normalize('NFD').replace(/\p{M}/gu, ''))
    }
  } catch {
    /* ignore */
  }

  const headers = {
    accept: 'application/vnd.linkedin.normalized+json+2.1',
    'csrf-token': csrf,
    'x-restli-protocol-version': '2.0.0',
    'x-li-lang': 'es_ES',
  }
  const decorations = [
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-128',
    'com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-16',
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardCore-16',
  ]

  // networkinfo primero (más directo para distance).
  for (const id of identities) {
    try {
      const u = `https://www.linkedin.com/voyager/api/identity/profiles/${encodeURIComponent(id)}/networkinfo`
      const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
      diag.http.push({ u: 'networkinfo', status: res.status })
      if (!res.ok) continue
      const data = await res.json()
      const v = extractDistanceFromVoyagerPayload(data, id)
      if (v) {
        diag.via = 'sw_networkinfo'
        await saveLastProbeDiag({ ...diag, verdict: v, ok: true })
        return v
      }
    } catch (err) {
      diag.http.push({ u: 'networkinfo', error: String(err?.message || err) })
    }
  }

  for (const id of identities) {
    try {
      const u = `https://www.linkedin.com/voyager/api/identity/profileView/${encodeURIComponent(id)}`
      const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
      diag.http.push({ u: 'profileView', status: res.status })
      if (!res.ok) continue
      const data = await res.json()
      const v = extractDistanceFromVoyagerPayload(data, id)
      if (v) {
        diag.via = 'sw_profileView'
        await saveLastProbeDiag({ ...diag, verdict: v, ok: true })
        return v
      }
    } catch (err) {
      diag.http.push({ u: 'profileView', error: String(err?.message || err) })
    }
  }

  for (const id of identities) {
    for (const decorationId of decorations) {
      try {
        const u =
          `https://www.linkedin.com/voyager/api/identity/dash/profiles` +
          `?q=memberIdentity&memberIdentity=${encodeURIComponent(id)}` +
          `&decorationId=${encodeURIComponent(decorationId)}`
        const res = await fetch(u, { method: 'GET', credentials: 'include', headers })
        diag.http.push({ u: 'dash', status: res.status, deco: decorationId.slice(-20) })
        if (!res.ok) continue
        const data = await res.json()
        const v = extractDistanceFromVoyagerPayload(data, id)
        if (v) {
          diag.via = 'sw_dash'
          await saveLastProbeDiag({ ...diag, verdict: v, ok: true })
          return v
        }
      } catch (err) {
        diag.http.push({ u: 'dash', error: String(err?.message || err) })
      }
    }
  }

  await saveLastProbeDiag({ ...diag, ok: false, error: 'voyager_no_distance' })
  return null
}

/**
 * Flujo: Voyager (cookie CSRF) primero → si hace falta, abrir perfil → FORCE → reportar.
 * No depende de ?nexus_probe= (LinkedIn lo suele borrar) ni de drafts.
 */
/**
 * Lee ·1er / ·2º / ·3er en el perfil abierto (MAIN world).
 * @returns {Promise<1|2|3|null>}
 */
async function readDegreeBadgeOnProfilePage() {
  const show = (degree, detail) => {
    if (!(degree === 1 || degree === 2 || degree === 3)) return
    try {
      const old = document.getElementById('nexus-degree-debug')
      if (old) old.remove()
      const el = document.createElement('div')
      el.id = 'nexus-degree-debug'
      const label = degree === 1 ? '1 (contacto)' : String(degree)
      el.textContent = detail ? `Nexus lee: ${label} · ${detail}` : `Nexus lee: ${label}`
      el.style.cssText =
        'position:fixed;z-index:2147483647;top:20px;left:50%;transform:translateX(-50%);' +
        'background:#0A66C2;color:#fff;padding:14px 22px;border-radius:12px;' +
        'font:700 18px/1.3 system-ui,sans-serif;box-shadow:0 10px 28px rgba(0,0,0,.4);'
      ;(document.documentElement || document.body).appendChild(el)
      window.setTimeout(() => {
        try {
          el.remove()
        } catch {
          /* ignore */
        }
      }, 2500)
    } catch {
      /* ignore */
    }
  }

  // NO usar \b después de º/° (rompe "· 2º").
  const has1 = (t) =>
    /[·•]\s*1(?:[\s.]*(?:er|ero|º|°|st|ro))?/i.test(t) ||
    /\b1(?:ero|er|st|ro)\b/i.test(t) ||
    /\b1[º°]/i.test(t) ||
    /\b1st\b/i.test(t)
  const has2 = (t) =>
    /[·•]\s*2(?:[\s.]*(?:º|°|nd|do))?/i.test(t) ||
    /\b2(?:do|nd)\b/i.test(t) ||
    /\b2[º°]/i.test(t) ||
    /\b2nd\b/i.test(t)
  const has3 = (t) =>
    /[·•]\s*3(?:[\s.]*(?:er|ero|º|°|rd|ro))?/i.test(t) ||
    /\b3(?:ero|er|ro|rd)\b/i.test(t) ||
    /\b3[º°]/i.test(t) ||
    /\b3rd\b/i.test(t)
  const degreeOf = (raw) => {
    const t = String(raw || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    if (!t) return null
    if (has1(t)) return 1
    if (has2(t)) return 2
    if (has3(t)) return 3
    return null
  }

  try {
    window.scrollTo(0, 0)
  } catch {
    /* ignore */
  }

  for (let i = 0; i < 12; i++) {
    if (/\/login|\/authwall|\/checkpoint/i.test(location.href)) return null
    const h1 =
      document.querySelector('main h1') ||
      document.querySelector('h1') ||
      document.querySelector('[data-anonymize="person-name"]')
    if (h1 && (h1.textContent || '').trim().length > 1) break
    await new Promise((r) => setTimeout(r, 200))
  }

  for (let i = 0; i < 10; i++) {
    for (const sel of [
      '.dist-value',
      '.distance-badge .dist-value',
      '.distance-badge',
      'span.artdeco-entity-lockup__degree',
      '[class*="distance-badge"]',
      '[class*="dist-value"]',
    ]) {
      for (const el of document.querySelectorAll(sel)) {
        const raw = (el.textContent || '').replace(/\s+/g, ' ').trim()
        if (!raw || raw.length > 24) continue
        const d = degreeOf(raw)
        if (d) {
          show(d, 'badge:' + raw)
          return d
        }
      }
    }

    const h1 =
      document.querySelector('main h1') ||
      document.querySelector('h1') ||
      document.querySelector('[data-anonymize="person-name"]')
    if (h1) {
      const box =
        h1.closest('section') ||
        h1.closest('[data-view-name="profile-top-card"]') ||
        h1.closest('.pv-top-card') ||
        h1.parentElement?.parentElement?.parentElement ||
        h1.parentElement
      const near = `${h1.textContent || ''} ${h1.parentElement?.innerText || ''} ${box?.innerText || ''}`
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 700)
      const d = degreeOf(near)
      if (d) {
        show(d, 'nombre')
        return d
      }
    }

    const roots = document.querySelectorAll(
      '.pvs-profile-actions, .pv-top-card-v2-ctas, [data-view-name="profile-top-card"], main section.artdeco-card, main',
    )
    for (const root of roots) {
      for (const el of root.querySelectorAll(
        'button, a[role="button"], a.artdeco-button, div[role="button"]',
      )) {
        const label = `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
          .toLowerCase()
          .replace(/\s+/g, ' ')
          .trim()
        if (!label || label.length > 90) continue
        if (/\b(pending|pendiente)\b/.test(label)) {
          show(2, 'pendiente')
          return 2
        }
        if (
          /^(contactar|conectar|connect)\b/.test(label) ||
          /\binvitar a conectar\b/.test(label)
        ) {
          show(2, 'btn-contactar')
          return 2
        }
      }
    }
    if (document.querySelector('a[href*="custom-invite"]')) {
      show(2, 'custom-invite')
      return 2
    }

    await new Promise((r) => setTimeout(r, 350))
  }
  return null
}

/**
 * Lectura como en secuencias individuales:
 * content-linkedin-connect → badge ·1er/·2º + Voyager (CSRF cookie) → POST.
 * Fallback: Voyager MAIN + DOM inyectado (tabs background).
 */
async function readAndReportDegreeViaContentScript(tabId, slug, prospectId) {
  const tid = Number(tabId || 0)
  if (!tid || !slug || !prospectId) return null
  await ensureConnectContentScript(tid)
  const attempts = []
  const csrfPresent = Boolean(await getLinkedInCsrfToken())

  // 1) Voyager primero (tabs background: badge a menudo no pinta).
  try {
    const csrf = await getLinkedInCsrfToken()
    if (!csrf) {
      attempts.push({ step: 'voyager_main', ok: false, error: 'sin_cookie_JSESSIONID' })
    } else {
      const apiV = await voyagerDistanceInTab(tid, slug, csrf)
      attempts.push({ step: 'voyager_main', ok: Boolean(apiV), verdict: apiV || null })
      if (apiV === 'connected' || apiV === 'not_connected') {
        const reported = await handleConnectionStatus({
          profileSlug: slug,
          status: apiV,
          prospectId,
        })
        return {
          ok: Boolean(reported?.ok),
          readOk: true,
          verdict: apiV,
          degree: apiV === 'connected' ? 1 : 2,
          connectionStatus: reported?.connectionStatus || apiV,
          via: 'voyager_main',
          csrf: true,
          attempts,
          error: reported?.ok ? null : reported?.error || 'report_failed',
        }
      }
    }
  } catch (err) {
    attempts.push({ step: 'voyager_main', ok: false, error: String(err?.message || err) })
  }

  // 2) Content script (badge + Voyager con CSRF del SW).
  try {
    const forced = await chrome.tabs.sendMessage(tid, {
      type: 'NEXUS_FORCE_CONNECTION_CHECK',
      prospectId,
      profileSlug: slug,
    })
    attempts.push({
      step: 'content_force',
      ok: Boolean(forced?.ok),
      verdict: forced?.verdict || null,
      degree: forced?.degree ?? null,
    })
    if (
      forced?.ok &&
      (forced.verdict === 'connected' || forced.verdict === 'not_connected')
    ) {
      const degree =
        Number(forced.degree) === 1 || Number(forced.degree) === 2 || Number(forced.degree) === 3
          ? Number(forced.degree)
          : forced.verdict === 'connected'
            ? 1
            : 2
      return {
        ok: true,
        readOk: true,
        verdict: forced.verdict,
        degree,
        connectionStatus: forced.connectionStatus || forced.verdict,
        via: 'content_force',
        csrf: csrfPresent,
        attempts,
      }
    }
  } catch (err) {
    attempts.push({ step: 'content_force', ok: false, error: String(err?.message || err) })
  }

  try {
    const read = await chrome.tabs.sendMessage(tid, {
      type: 'NEXUS_READ_CONNECTION_VERDICT',
      profileSlug: slug,
      fast: true,
    })
    attempts.push({
      step: 'content_read',
      ok: Boolean(read?.verdict),
      verdict: read?.verdict || null,
      degree: read?.degree ?? null,
    })
    if (read?.verdict === 'connected' || read?.verdict === 'not_connected') {
      const reported = await handleConnectionStatus({
        profileSlug: slug,
        status: read.verdict,
        prospectId,
      })
      const degree =
        Number(read.degree) === 1 || Number(read.degree) === 2 || Number(read.degree) === 3
          ? Number(read.degree)
          : read.verdict === 'connected'
            ? 1
            : 2
      return {
        ok: Boolean(reported?.ok),
        readOk: true,
        verdict: read.verdict,
        degree,
        connectionStatus: reported?.connectionStatus || read.verdict,
        via: 'content_read',
        csrf: csrfPresent,
        attempts,
        error: reported?.ok ? null : reported?.error || 'report_failed',
      }
    }
  } catch (err) {
    attempts.push({ step: 'content_read', ok: false, error: String(err?.message || err) })
  }

  // 3) Badge DOM inyectado.
  try {
    const injected = await chrome.scripting.executeScript({
      target: { tabId: tid },
      world: 'MAIN',
      func: readDegreeBadgeOnProfilePage,
    })
    const n = injected?.[0]?.result
    attempts.push({ step: 'badge_main', ok: n === 1 || n === 2 || n === 3, degree: n ?? null })
    if (n === 1 || n === 2 || n === 3) {
      const verdict = n === 1 ? 'connected' : 'not_connected'
      const reported = await handleConnectionStatus({
        profileSlug: slug,
        status: verdict,
        prospectId,
      })
      return {
        ok: Boolean(reported?.ok),
        readOk: true,
        verdict,
        degree: n,
        connectionStatus: reported?.connectionStatus || verdict,
        via: 'badge_main',
        csrf: csrfPresent,
        attempts,
        error: reported?.ok ? null : reported?.error || 'report_failed',
      }
    }
  } catch (err) {
    attempts.push({ step: 'badge_main', ok: false, error: String(err?.message || err) })
  }
  return {
    ok: false,
    readOk: false,
    error: csrfPresent ? 'no_degree_badge_ni_voyager' : 'sin_cookie_JSESSIONID',
    via: 'all_failed',
    csrf: csrfPresent,
    attempts,
  }
}

/**
 * Mismo camino que secuencias individuales (el que ya leía 1º/2º/3º):
 * abrir perfil → NEXUS_FORCE_CONNECTION_CHECK (badge + Voyager en content script) → reportar → cerrar.
 * Sin inyecciones MAIN lentas que hacen timeout_extension_probe.
 * Foco: abre en background y restaura Nexus (no deja al usuario en LinkedIn).
 */
async function probeOneConnection(item, { allowCreateTab = false, skipCooldown = false } = {}) {
  if (LI_SAFE_NO_PROFILE_PROBE) {
    return {
      ok: true,
      skipped: true,
      reason: 'li_safe_no_probe',
      readOk: false,
      prospectId: Number(item?.prospect_id || 0) || null,
    }
  }
  const url = normalizeProfileUrl(item?.linkedin_url)
  const prospectId = Number(item?.prospect_id || 0)
  const prospectName = String(item?.prospect_name || '').trim()
  const connStatus = String(item?.connection_status || '').toLowerCase()
  if (!url || !prospectId) return { ok: false, readOk: false, error: 'missing_url_or_id' }

  const slug = slugFromProfileUrl(url)
  const isChecking = connStatus === 'checking' || connStatus === '' || connStatus === 'none'
  if (!isChecking || !allowCreateTab) {
    return { ok: false, skipped: true, reason: 'test_only_checking' }
  }
  if (await wasProbeResolved(prospectId)) {
    return { ok: true, skipped: true, reason: 'already_resolved', prospectId }
  }
  if (await isLinkedInAssistBusy({ ignorePendingSend: true })) {
    return { ok: false, skipped: true, reason: 'assist_busy' }
  }
  if (!skipCooldown && (await wasProbedRecently(prospectId))) {
    return { ok: false, skipped: true, reason: 'cooldown' }
  }
  await markProbedAttempt(prospectId)

  const nexusFocus = (await captureUiFocus()) || (await findNexusTabFocus())
  /** @type {number|null} */
  let ourTabId = null
  const startedAt = Date.now()
  // Hidratar + paint + Voyager + badge/CTA + 2ª pasada caben en ~28s (UI ~32–40s).
  const hardDeadlineMs = 28_000

  /** Lee sin reportar (evita deadlock SW↔content). El SW reporta después. */
  const readVerdictOnly = async (tabId, { quick = true } = {}) => {
    await ensureConnectContentScript(tabId)
    try {
      return await chrome.tabs.sendMessage(tabId, {
        type: 'NEXUS_READ_CONNECTION_VERDICT',
        profileSlug: slug,
        fast: true,
        quick: Boolean(quick),
      })
    } catch {
      await ensureConnectContentScript(tabId)
      try {
        return await chrome.tabs.sendMessage(tabId, {
          type: 'NEXUS_READ_CONNECTION_VERDICT',
          profileSlug: slug,
          fast: true,
          quick: Boolean(quick),
        })
      } catch (err) {
        return { ok: false, error: String(err?.message || err || 'content_script_no_responde') }
      }
    }
  }

  try {
    const probeUrl = withNexusProbeParam(url, String(prospectId))
    await markProbeTabOpened(prospectId)
    await saveLastProbeDiag({
      ok: false,
      readOk: false,
      phase: 'opening',
      summary: prospectName
        ? `Leyendo… · ${prospectName} (perfil)`
        : `Leyendo… · #${prospectId} (perfil)`,
      prospectId,
      prospectName,
    })

    let existing = null
    for (const [tid, info] of nexusOwnedProbeTabs.entries()) {
      if (Number(info?.prospectId || 0) !== prospectId) continue
      try {
        existing = await chrome.tabs.get(tid)
        if (existing?.id) break
      } catch {
        await forgetProbeTab(tid)
      }
    }
    if (!existing?.id) {
      existing = await findExistingProbeTab(prospectId, slug, probeUrl)
    }

    if (existing?.id) {
      ourTabId = Number(existing.id)
      await rememberProbeTab(ourTabId, prospectId, slug)
    } else {
      const tab = await openProbeTabInBackground(probeUrl, nexusFocus, { prospectId, slug })
      ourTabId = tab?.id ? Number(tab.id) : null
      if (!ourTabId) {
        const fail = { ok: false, readOk: false, error: 'no_tab', prospectId, prospectName }
        await saveLastProbeDiag(fail)
        return fail
      }
      await rememberProbeTab(ourTabId, prospectId, slug)
    }
    await restoreUiFocus(nexusFocus)

    await waitForTabReady(ourTabId, 10_000)
    await waitForLinkedInProfileHydrated(ourTabId, 8_000)
    await sleep(800)
    await restoreUiFocus(nexusFocus)

    // Para badge ·1er/·2º LinkedIn necesita pintar: micro-activar ~1.2s y volver a Nexus.
    try {
      await chrome.tabs.update(ourTabId, { active: true })
      await sleep(1_200)
    } catch {
      /* ignore */
    }
    await restoreUiFocus(nexusFocus)

    const csrf = await getLinkedInCsrfToken()
    await saveLastProbeDiag({
      ok: false,
      readOk: false,
      phase: 'reading',
      summary: prospectName
        ? `Leyendo… · ${prospectName} (Voyager + badge)`
        : `Leyendo… · #${prospectId} (Voyager + badge)`,
      prospectId,
      prospectName,
      csrf: Boolean(csrf),
      via: 'voyager_badge',
    })

    /** @type {string|null} */
    let verdict = null
    /** @type {number|null} */
    let degree = null
    /** @type {string} */
    let via = 'none'
    const attempts = []

    // 1) Badge / CTA en DOM (tras paint) — CTA Contactar veta 1º.
    if (Date.now() - startedAt < hardDeadlineMs) {
      const badge = await readDegreeOrCtaOnProfile(ourTabId)
      attempts.push({
        step: 'badge_cta',
        ok: Boolean(badge?.degree),
        degree: badge?.degree ?? null,
        detail: badge?.via || null,
      })
      if (badge?.degree === 2 || badge?.degree === 3) {
        degree = badge.degree
        verdict = 'not_connected'
        via = `badge_${badge.via || 'dom'}`
      } else if (badge?.degree === 1) {
        // 1º desde DOM: si Voyager dice 2º/3º, Voyager gana (anti falso contacto).
        if (csrf) {
          const apiV = await voyagerDistanceInTab(ourTabId, slug, csrf)
          attempts.push({
            step: 'voyager_confirm_1',
            ok: Boolean(apiV),
            verdict: apiV || null,
          })
          if (apiV === 'not_connected') {
            degree = 2
            verdict = 'not_connected'
            via = 'voyager_over_false_1'
          } else {
            degree = 1
            verdict = 'connected'
            via = apiV === 'connected' ? 'badge_voyager_1' : `badge_${badge.via || 'dom'}`
          }
        } else {
          degree = 1
          verdict = 'connected'
          via = `badge_${badge.via || 'dom'}`
        }
      }
    }

    // 2) Voyager (CSRF cookie) en la pestaña.
    if (!verdict && csrf && Date.now() - startedAt < hardDeadlineMs) {
      const apiV = await voyagerDistanceInTab(ourTabId, slug, csrf)
      attempts.push({ step: 'voyager_sw', ok: Boolean(apiV), verdict: apiV || null })
      if (apiV === 'connected' || apiV === 'not_connected') {
        // Si Voyager dice 1º, re-chequear CTA (Contactar = no contacto).
        if (apiV === 'connected') {
          const badge2 = await readDegreeOrCtaOnProfile(ourTabId)
          if (badge2?.degree === 2 || badge2?.degree === 3) {
            degree = badge2.degree
            verdict = 'not_connected'
            via = `cta_veto_voyager_${badge2.via || 'dom'}`
          } else {
            verdict = apiV
            degree = 1
            via = 'voyager_sw'
          }
        } else {
          verdict = apiV
          degree = 2
          via = 'voyager_sw'
        }
      }
    } else if (!verdict && !csrf) {
      attempts.push({ step: 'voyager_sw', ok: false, error: 'sin_cookie_JSESSIONID' })
    }

    // 3) Content READ (Voyager+badge del content script, sin reportar).
    if (!verdict && Date.now() - startedAt < hardDeadlineMs) {
      const read = await readVerdictOnly(ourTabId, { quick: false })
      attempts.push({
        step: 'content_read',
        ok: Boolean(read?.verdict),
        verdict: read?.verdict || null,
        degree: read?.degree ?? null,
        error: read?.error || null,
      })
      if (read?.verdict === 'connected' || read?.verdict === 'not_connected') {
        verdict = read.verdict
        degree =
          Number(read.degree) === 1 || Number(read.degree) === 2 || Number(read.degree) === 3
            ? Number(read.degree)
            : read.verdict === 'connected'
              ? 1
              : 2
        via = 'content_read'
      }
    }

    // 4) Segunda pasada badge si aún nada.
    if (!verdict && Date.now() - startedAt < hardDeadlineMs) {
      try {
        await chrome.tabs.update(ourTabId, { active: true })
        await sleep(800)
      } catch {
        /* ignore */
      }
      await restoreUiFocus(nexusFocus)
      const badge2 = await readDegreeOrCtaOnProfile(ourTabId)
      attempts.push({
        step: 'badge_cta_2',
        ok: Boolean(badge2?.degree),
        degree: badge2?.degree ?? null,
        detail: badge2?.via || null,
      })
      if (badge2?.degree === 1 || badge2?.degree === 2 || badge2?.degree === 3) {
        degree = badge2.degree
        verdict = badge2.degree === 1 ? 'connected' : 'not_connected'
        via = `badge2_${badge2.via || 'dom'}`
      }
    }

    let reported = null
    if (verdict === 'connected' || verdict === 'not_connected') {
      reported = await handleConnectionStatus({
        profileSlug: slug,
        status: verdict,
        prospectId,
      })
    }

    const idNow = ourTabId
    ourTabId = null
    if (idNow) await closeOurCreatedProbeTab(idNow)
    await clearProbeTabOpened(prospectId)
    await restoreUiFocus(nexusFocus)

    if (reported?.ok && (verdict === 'connected' || verdict === 'not_connected')) {
      const deg = degree === 1 || degree === 2 || degree === 3 ? degree : verdict === 'connected' ? 1 : 2
      await markProbeResolved(prospectId, deg === 1 ? 1 : 2)
      const result = {
        ok: true,
        readOk: true,
        created: true,
        closed: true,
        degree: deg,
        verdict,
        reported: true,
        connectionStatus: reported?.connectionStatus || verdict,
        via,
        csrf: Boolean(csrf),
        attempts,
        prospectId,
        prospectName,
      }
      await saveLastProbeDiag(result)
      return result
    }

    const fail = {
      ok: false,
      readOk: false,
      created: true,
      closed: true,
      error: !csrf
        ? 'sin_cookie_JSESSIONID'
        : verdict
          ? reported?.error || 'report_failed'
          : 'no_verdict',
      via,
      csrf: Boolean(csrf),
      attempts,
      prospectId,
      prospectName,
    }
    await saveLastProbeDiag(fail)
    scheduleProbeFollowup(true)
    return fail
  } catch (err) {
    const fail = {
      ok: false,
      readOk: false,
      error: String(err?.message || err),
      created: Boolean(ourTabId),
      closed: false,
      prospectId,
      prospectName,
      via: 'voyager_badge',
    }
    try {
      if (ourTabId) await closeOurCreatedProbeTab(ourTabId)
    } catch {
      /* ignore */
    }
    ourTabId = null
    await clearProbeTabOpened(prospectId)
    await restoreUiFocus(nexusFocus)
    await saveLastProbeDiag(fail)
    scheduleProbeFollowup(true)
    return fail
  }
}

async function isLinkedInAssistBusy({ ignorePendingSend = false } = {}) {
  try {
    const session = await chrome.storage.session.get([OPEN_CHAT_KEY, ASSIST_KEY])
    const openChat = session?.[OPEN_CHAT_KEY]
    if (openChat?.startedAt && Date.now() - Number(openChat.startedAt || 0) < 90_000) {
      return true
    }
    const assist = session?.[ASSIST_KEY]
    if (assist?.startedAt && Date.now() - Number(assist.startedAt || 0) < 90_000) {
      return true
    }
    if (!ignorePendingSend) {
      const local = await chrome.storage.local.get(PENDING_KEY)
      const pending = local?.[PENDING_KEY]
      if (pending?.since && Date.now() - Number(pending.since) < 120_000) return true
    }
  } catch {
    /* ignore */
  }
  return false
}

/** Cierra únicamente el tabId que devolvió tabs.create en este probe. */
async function closeOnlyOurProbeTab(tabId) {
  const id = Number(tabId || 0)
  if (!id) return
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await chrome.tabs.remove(id)
      return
    } catch {
      await sleep(200)
    }
  }
}

/** Guarda la pestaña/ventana enfocada (p.ej. Nexus) para no perder el foco. */
async function captureUiFocus() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true })
    if (!tab?.id) return null
    return { tabId: tab.id, windowId: tab.windowId || null }
  } catch {
    return null
  }
}

/** Preferir volver a una pestaña Nexus (localhost) si existe. */
async function findNexusTabFocus() {
  try {
    const tabs = await chrome.tabs.query({
      url: ['http://127.0.0.1/*', 'http://localhost/*'],
    })
    const hit = (tabs || []).find((t) => t?.id && /nexus|5173|5174|3000/i.test(String(t.url || '')))
      || (tabs || []).find((t) => t?.id)
    if (!hit?.id) return null
    return { tabId: hit.id, windowId: hit.windowId || null }
  } catch {
    return null
  }
}

async function restoreUiFocus(focus) {
  const target = focus || (await findNexusTabFocus())
  if (!target?.tabId) return
  try {
    if (target.windowId) {
      await chrome.windows.update(target.windowId, { focused: true })
    }
  } catch {
    /* ignore */
  }
  try {
    await chrome.tabs.update(target.tabId, { active: true })
  } catch {
    /* ignore */
  }
}

/** Abrir perfil SIN robar foco; devolver Nexus al toque. Reusa tab probe si ya existe. */
async function findExistingProbeTab(prospectId, slug, probeUrl) {
  const pid = Number(prospectId || 0)
  const wantSlug = String(slug || '')
    .trim()
    .toLowerCase()
  for (const [tid, info] of nexusOwnedProbeTabs.entries()) {
    if (pid && Number(info?.prospectId || 0) !== pid) continue
    try {
      const t = await chrome.tabs.get(tid)
      if (t?.id) return t
    } catch {
      await forgetProbeTab(tid)
    }
  }
  try {
    const tabs = await chrome.tabs.query({ url: ['https://www.linkedin.com/in/*'] })
    for (const t of tabs || []) {
      const u = String(t.url || '')
      if (pid && new RegExp(`[?&]nexus_probe=${pid}(?:&|$)`, 'i').test(u)) return t
    }
    if (wantSlug) {
      for (const t of tabs || []) {
        const u = String(t.url || '').toLowerCase()
        if (u.includes(`/in/${wantSlug}`) && /[?&]nexus_probe=/i.test(u)) return t
      }
    }
  } catch {
    /* ignore */
  }
  void probeUrl
  return null
}

async function openProbeTabInBackground(probeUrl, nexusFocus, { prospectId = 0, slug = '' } = {}) {
  const existing = await findExistingProbeTab(prospectId, slug, probeUrl)
  if (existing?.id) {
    try {
      // Reusar: no crear segunda pestaña. Actualizar URL si hace falta.
      const cur = String(existing.url || '')
      if (prospectId && !new RegExp(`[?&]nexus_probe=${prospectId}(?:&|$)`, 'i').test(cur)) {
        await chrome.tabs.update(existing.id, { url: probeUrl, active: false })
      }
    } catch {
      /* ignore */
    }
    await restoreUiFocus(nexusFocus)
    return existing
  }
  const tab = await chrome.tabs.create({ url: probeUrl, active: false })
  // Devolver foco YA (antes de que LinkedIn cargue).
  await restoreUiFocus(nexusFocus)
  await sleep(80)
  await restoreUiFocus(nexusFocus)
  return tab
}

/** no-op: cerrar por slug/ids viejos mataba el LinkedIn manual. */
async function closeStrayProbeTabsForSlug(_slug) {}

/** Asegura que content-linkedin-connect.js esté vivo en la pestaña. */
async function ensureLinkedInConnectScript(tabId) {
  if (!tabId) return false
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'NEXUS_PING_CONNECT' })
    return true
  } catch {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ['content-linkedin-connect.js'],
      })
      await sleep(350)
      return true
    } catch {
      return false
    }
  }
}
