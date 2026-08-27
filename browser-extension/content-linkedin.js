/**
 * Abre el chat de LinkedIn leyendo el href real del botón «Mensaje».
 *
 * Lógica general (vale para CUALQUIER perfil):
 * 1) En /in/... buscar a[href*="/messaging/compose"]
 * 2) Extraer recipient / profileUrn (ACoAA…)
 * 3) Armar compose limpio y navegar (location.assign)
 * 4) Avisar al background para guardar el URN en Nexus
 *
 * Los /messaging/thread/... son IDs de conversación (UUID), NO se arman
 * desde el nombre del contacto.
 */
const OPEN_CHAT_KEY = 'nexusLinkedInOpenChat'
const MAX_MS = 120000

let busy = false
let lastNavAt = 0
let lastToast = ''
let learnedForJob = null

void tick()
setInterval(() => void tick(), 280)

let lastHref = location.href
setInterval(() => {
  if (location.href !== lastHref) {
    lastHref = location.href
    void tick()
  }
}, 200)

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes[OPEN_CHAT_KEY]) {
    learnedForJob = null
    void tick()
  }
})

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'NEXUS_OPEN_CHAT_NOW') {
    void attemptOpenChat()
      .then((result) => sendResponse(result))
      .catch(() => sendResponse({ opened: false }))
    return true
  }
  if (message?.type === 'NEXUS_ASSIST_STATUS') {
    showToast(message.status || 'Nexus')
    return false
  }
})

try {
  const root = document.documentElement
  if (root) {
    new MutationObserver(() => void tick()).observe(root, { childList: true, subtree: true })
  }
} catch {
  /* ignore */
}

async function tick() {
  if (busy) return
  const job = await loadJob()
  if (!job) return
  busy = true
  try {
    await attemptOpenChat()
  } finally {
    busy = false
  }
}

async function loadJob() {
  const stored = await chrome.storage.session.get(OPEN_CHAT_KEY)
  const job = stored?.[OPEN_CHAT_KEY]
  if (!job?.profileUrl && !job?.profileSlug) return null
  if (Date.now() - Number(job.startedAt || 0) > MAX_MS) {
    await chrome.storage.session.remove(OPEN_CHAT_KEY)
    return null
  }
  return job
}

async function attemptOpenChat() {
  const job = await loadJob()
  if (!job) return { opened: false }

  const path = location.pathname.toLowerCase()

  if (path.includes('/messaging')) {
    await chrome.storage.session.remove(OPEN_CHAT_KEY)
    showToast('Chat abierto ✓')
    return { opened: true }
  }

  if (!path.startsWith('/in/')) {
    return { opened: false, reason: 'not_on_profile' }
  }

  if (!profileMatchesJob(job)) {
    showToast('Nexus: esperando el perfil del prospecto…')
    return { opened: false, reason: 'wrong_profile' }
  }

  showToast('Nexus: buscando botón Mensaje…')

  const fromDom = findComposeUrlInDom()
  if (fromDom) {
    return await goToCompose(job, fromDom, 'dom-message-button')
  }

  // Reintentos cortos: LinkedIn hidrata el CTA tarde.
  for (let i = 0; i < 8; i += 1) {
    await sleep(350)
    const again = findComposeUrlInDom()
    if (again) {
      return await goToCompose(job, again, 'dom-message-button')
    }
  }

  return { opened: false, reason: 'no_message_button' }
}

async function goToCompose(job, composeUrl, method) {
  const urn = extractUrnFromComposeUrl(composeUrl)
  const clean = urn ? buildComposeUrl(urn) : normalizeComposeHref(composeUrl)
  if (!clean) return { opened: false, reason: 'bad_compose' }

  const jobKey = `${job.profileSlug || ''}|${job.prospectId || ''}|${urn || clean}`
  if (learnedForJob !== jobKey) {
    learnedForJob = jobKey
    chrome.runtime
      .sendMessage({
        type: 'NEXUS_LEARNED_PROFILE_URN',
        profileSlug: job.profileSlug || currentSlug(),
        prospectId: job.prospectId || null,
        urn,
        composeUrl: clean,
        method,
      })
      .catch(() => {})
  }

  showToast('Abriendo chat…')
  navigate(clean)
  return { navigating: true, composeUrl: clean, urn, method }
}

function findComposeUrlInDom() {
  const anchors = [
    ...document.querySelectorAll(
      [
        'a[href*="/messaging/compose"]',
        '.pvs-profile-actions a[href*="messaging"]',
        'main a[href*="/messaging/compose"]',
        'section a[href*="/messaging/compose"]',
      ].join(', '),
    ),
  ]
  for (const el of anchors) {
    const href = el.getAttribute('href') || el.href
    const url = normalizeComposeHref(href)
    if (url) return url
  }
  // A veces el CTA está en el HTML pero aún no “visible”.
  try {
    const html = document.documentElement?.innerHTML || ''
    const m = html.match(/href="(\/messaging\/compose\/?[^"]+)"/i)
    if (m) return normalizeComposeHref(m[1])
  } catch {
    /* ignore */
  }
  return null
}

function extractUrnFromComposeUrl(raw) {
  try {
    const url = new URL(raw, location.origin)
    const recipient = url.searchParams.get('recipient')
    if (recipient && /^[A-Za-z0-9_-]{10,}$/.test(recipient)) return recipient
    const profileUrn = decodeURIComponent(url.searchParams.get('profileUrn') || '')
    const m = profileUrn.match(/urn:li:fsd_profile:([A-Za-z0-9_-]+)/i)
    if (m) return m[1]
  } catch {
    /* ignore */
  }
  const m2 = String(raw || '').match(/fsd_profile[%3A:]+([A-Za-z0-9_-]+)/i)
  return m2 ? m2[1] : null
}

function buildComposeUrl(urnId) {
  const id = String(urnId || '').trim()
  if (!id) return null
  const encoded = encodeURIComponent(`urn:li:fsd_profile:${id}`)
  return (
    `https://www.linkedin.com/messaging/compose/` +
    `?profileUrn=${encoded}` +
    `&recipient=${encodeURIComponent(id)}` +
    `&screenContext=NON_SELF_PROFILE_VIEW` +
    `&interop=msgOverlay`
  )
}

function normalizeComposeHref(href) {
  if (!href) return null
  try {
    const url = new URL(href, location.origin)
    if (!url.pathname.includes('/messaging/compose') && !url.searchParams.has('profileUrn')) {
      return null
    }
    if (!url.pathname.includes('/messaging/compose')) {
      url.pathname = '/messaging/compose/'
    }
    // Limpiar lipi y basura de tracking; mantener URN.
    const urn =
      url.searchParams.get('recipient') ||
      extractUrnFromComposeUrl(url.toString())
    if (urn) return buildComposeUrl(urn)
    if (!url.searchParams.has('interop')) url.searchParams.set('interop', 'msgOverlay')
    if (!url.searchParams.has('screenContext')) {
      url.searchParams.set('screenContext', 'NON_SELF_PROFILE_VIEW')
    }
    url.searchParams.delete('lipi')
    return url.toString()
  } catch {
    return null
  }
}

function navigate(url) {
  const target = String(url || '').trim()
  if (!target) return
  if (Date.now() - lastNavAt < 500) return
  lastNavAt = Date.now()
  location.assign(target)
}

function profileMatchesJob(job) {
  const want = String(job.profileSlug || slugFromUrl(job.profileUrl) || '').toLowerCase()
  const cur = currentSlug()
  const href = location.href.toLowerCase()
  if (!want) return true
  if (cur && slugMatch(cur, want)) return true
  // Cola de id del slug (ej. 62638b70) — robusto con acentos / encoding.
  const tail = want.split('-').pop()
  if (tail && tail.length >= 6 && (href.includes(tail) || (cur || '').includes(tail))) return true
  return false
}

function currentSlug() {
  const m = location.pathname.match(/\/in\/([^/?#]+)/i)
  if (!m) return null
  try {
    return decodeURIComponent(m[1]).toLowerCase()
  } catch {
    return m[1].toLowerCase()
  }
}

function slugFromUrl(raw) {
  try {
    const m = new URL(raw).pathname.match(/\/in\/([^/?#]+)/i)
    if (!m) return null
    return decodeURIComponent(m[1]).toLowerCase()
  } catch {
    return null
  }
}

function slugMatch(a, b) {
  const left = String(a || '').toLowerCase()
  const right = String(b || '').toLowerCase()
  if (!left || !right) return true
  if (left === right) return true
  const strip = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  if (strip(left) === strip(right)) return true
  const lt = left.split('-').pop()
  const rt = right.split('-').pop()
  return Boolean(lt && rt && lt.length >= 6 && lt === rt)
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

function showToast(text) {
  if (text === lastToast) return
  lastToast = text
  try {
    const id = 'nexus-li-chat-toast'
    let el = document.getElementById(id)
    if (!el) {
      el = document.createElement('div')
      el.id = id
      el.style.cssText =
        'position:fixed;z-index:2147483647;top:16px;right:16px;max-width:340px;' +
        'background:#0A66C2;color:#fff;padding:12px 16px;border-radius:10px;' +
        'font:600 13px/1.35 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25)'
      document.documentElement.appendChild(el)
    }
    el.textContent = text
    setTimeout(() => {
      if (el?.textContent === text) el?.remove()
    }, 7000)
  } catch {
    /* ignore */
  }
}
