/**
 * Utilidades DOM para abrir el chat de LinkedIn (inyectadas vía chrome.scripting).
 * Preferimos navegar a /messaging/compose?profileUrn=... antes que simular clicks:
 * LinkedIn React suele ignorar clicks desde mundos de extensión.
 */
window.__NEXUS_LI_ASSIST__ = {
  resolveChatOpen,
  isChatOpen,
  pasteComposerMessage,
  extractAssistTarget,
  findComposeBox,
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function isVisible(el) {
  if (!el) return false
  const style = window.getComputedStyle(el)
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
    return false
  }
  const rect = el.getBoundingClientRect()
  return rect.width > 0 && rect.height > 0
}

function parseUrnId(raw) {
  const value = String(raw || '').trim()
  if (!value) return null
  const decoded = decodeURIComponent(value)
  const fromUrn = decoded.match(/urn:li:fsd_profile:([A-Za-z0-9_-]+)/)
  if (fromUrn) return fromUrn[1]
  if (/^[A-Za-z0-9_-]{10,}$/.test(value)) return value
  return null
}

function buildComposeUrl(profileUrnId) {
  const id = parseUrnId(profileUrnId)
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

function toAbsoluteComposeUrl(linkOrHref) {
  const raw =
    typeof linkOrHref === 'string'
      ? linkOrHref
      : linkOrHref?.href || linkOrHref?.getAttribute?.('href') || ''
  if (!raw) return null
  try {
    const url = new URL(raw, window.location.origin)
    if (!url.pathname.includes('/messaging/compose')) return null
    if (!url.searchParams.has('interop')) url.searchParams.set('interop', 'msgOverlay')
    if (!url.searchParams.has('screenContext')) {
      url.searchParams.set('screenContext', 'NON_SELF_PROFILE_VIEW')
    }
    return url.toString()
  } catch {
    return null
  }
}

function currentProfileSlug() {
  try {
    const m = window.location.pathname.match(/\/in\/([^/?#]+)/i)
    if (!m) return null
    return decodeURIComponent(m[1]).replace(/\/+$/, '').toLowerCase()
  } catch {
    return null
  }
}

function normalizeSlug(value) {
  try {
    return decodeURIComponent(String(value || ''))
      .replace(/\/+$/, '')
      .trim()
      .toLowerCase()
  } catch {
    return String(value || '')
      .trim()
      .toLowerCase()
  }
}

function slugsMatch(a, b) {
  const left = normalizeSlug(a)
  const right = normalizeSlug(b)
  if (!left || !right) return false
  if (left === right) return true
  // LinkedIn a veces cambia acentos / encoding entre URL y JSON.
  const strip = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  if (strip(left) === strip(right)) return true
  // Match por cola de id (ej. ...-62638b70)
  const leftTail = left.split('-').pop()
  const rightTail = right.split('-').pop()
  if (leftTail && rightTail && leftTail.length >= 6 && leftTail === rightTail) return true
  return false
}

function isMessageLabel(label) {
  const text = String(label || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  if (!text) return false
  if (text === 'mensaje' || text === 'message' || text === 'send message' || text === 'enviar mensaje') {
    return true
  }
  if (!(text.includes('mensaje') || text.includes('message'))) return false
  if (
    text.includes('messaging') ||
    text.includes('inmail') ||
    text.includes('ver mensajes') ||
    text.includes('view messages') ||
    text.includes('mensajes de') ||
    text.startsWith('mensajes') ||
    text.startsWith('messages')
  ) {
    return false
  }
  return /\b(mensaje|message)\b/.test(text)
}

function findBestComposeLink() {
  const links = [...document.querySelectorAll('a[href*="/messaging/compose"]')].filter(isVisible)
  if (links.length === 0) return null

  const messageLink = links.find((link) =>
    isMessageLabel(link.getAttribute('aria-label') || link.textContent),
  )
  return messageLink || links[0]
}

function profileActionRoots() {
  const roots = [
    ...document.querySelectorAll(
      [
        '.pvs-profile-actions',
        '.pv-top-card-v2-ctas',
        '.ph5.pb5',
        'main .pv-top-card',
        'section.artdeco-card .ph5',
        '[data-view-name="profile-main-level"]',
      ].join(', '),
    ),
  ]
  return roots.length ? roots : [document]
}

function findMessageButton() {
  const selectors = [
    'a[href*="/messaging/compose"]',
    'button[aria-label*="Enviar un mensaje" i]',
    'button[aria-label*="Send a message" i]',
    'button[aria-label*="mensaje" i]',
    'button[aria-label*="Message" i]',
    'a[aria-label*="Enviar un mensaje" i]',
    'a[aria-label*="mensaje" i]',
    'a[aria-label*="Message" i]',
    'button',
    'a',
    'div[role="button"]',
  ]

  for (const root of profileActionRoots()) {
    for (const sel of selectors) {
      let nodes = []
      try {
        nodes = [...root.querySelectorAll(sel)]
      } catch {
        continue
      }
      for (const el of nodes) {
        if (!isVisible(el)) continue
        if (el.matches?.('a[href*="/messaging/compose"]')) return el
        const label = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''} ${el.textContent || ''}`
        if (isMessageLabel(label)) return el
      }
    }
  }
  for (const el of document.querySelectorAll('button, a, div[role="button"]')) {
    if (!isVisible(el)) continue
    const label = `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
    if (isMessageLabel(label)) return el
  }
  return null
}

function findMoreActionsButton() {
  for (const root of profileActionRoots()) {
    for (const el of root.querySelectorAll('button, div[role="button"]')) {
      if (!isVisible(el)) continue
      const label = `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
        .toLowerCase()
        .replace(/\s+/g, ' ')
      if (
        label.includes('más') ||
        label.includes('more') ||
        label.includes('other actions') ||
        label.includes('otras acciones')
      ) {
        return el
      }
    }
  }
  return null
}

function findMessageInOpenDropdown() {
  const items = document.querySelectorAll(
    '[role="menu"] [role="menuitem"], .artdeco-dropdown__content li, .artdeco-dropdown__item, div[role="button"]',
  )
  for (const el of items) {
    if (!isVisible(el)) continue
    const label = `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
    if (isMessageLabel(label)) return el
  }
  return null
}

function simulateClick(el) {
  if (!el) return
  el.scrollIntoView({ block: 'center', behavior: 'instant' })
  const opts = { bubbles: true, cancelable: true, view: window }
  try {
    el.dispatchEvent(new PointerEvent('pointerdown', opts))
  } catch {
    /* ignore */
  }
  el.dispatchEvent(new MouseEvent('mousedown', opts))
  try {
    el.dispatchEvent(new PointerEvent('pointerup', opts))
  } catch {
    /* ignore */
  }
  el.dispatchEvent(new MouseEvent('mouseup', opts))
  el.dispatchEvent(new MouseEvent('click', opts))
  if (typeof el.click === 'function') el.click()
}

function findComposeBox() {
  const selectors = [
    'div.msg-form__contenteditable[contenteditable="true"]',
    'div.msg-overlay-conversation-bubble div[contenteditable="true"]',
    'div.msg-overlay-conversation-bubble--theme-container div[contenteditable="true"]',
    'div.msg-overlay div[contenteditable="true"]',
    '.msg-form__msg-content-container div[contenteditable="true"]',
    'form.msg-form div[contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[contenteditable="true"][data-placeholder]',
    'div[contenteditable="true"][aria-label*="mensaje" i]',
    'div[contenteditable="true"][aria-label*="message" i]',
  ]
  for (const sel of selectors) {
    const el = [...document.querySelectorAll(sel)].find((node) => isVisible(node))
    if (el) return el
  }
  return null
}

function isChatOpen() {
  if (findComposeBox()) return true
  // Compose route cuenta como chat aunque el editor aún hydratee.
  return window.location.pathname.toLowerCase().includes('/messaging')
}

function pageHtmlBlob(maxLen = 700000) {
  const chunks = []
  try {
    chunks.push(document.documentElement?.innerHTML?.slice(0, maxLen) || '')
  } catch {
    /* ignore */
  }
  for (const script of document.querySelectorAll('script')) {
    const t = script.textContent || ''
    if (
      t.includes('fsd_profile') ||
      t.includes('publicIdentifier') ||
      t.includes('profileUrn') ||
      t.includes('entityUrn')
    ) {
      chunks.push(t.slice(0, 250000))
    }
  }
  return chunks.join('\n')
}

/**
 * CRÍTICO: no usar el URN más frecuente de la página (suele ser el del usuario logueado).
 * Buscar el URN asociado al publicIdentifier / slug del perfil abierto.
 */
function extractUrnForCurrentProfile() {
  const slug = currentProfileSlug()
  const blob = pageHtmlBlob()
  if (!blob) return null

  if (slug) {
    // publicIdentifier cerca de entityUrn / profileUrn (ambos órdenes).
    const pairPatterns = [
      /"publicIdentifier"\s*:\s*"([^"]+)"[\s\S]{0,500}?"(?:\*profileUrn|profileUrn|entityUrn)"\s*:\s*"(?:urn:li:fsd_profile:)?([^"]+)"/g,
      /"(?:\*profileUrn|profileUrn|entityUrn)"\s*:\s*"(?:urn:li:fsd_profile:)?([^"]+)"[\s\S]{0,500}?"publicIdentifier"\s*:\s*"([^"]+)"/g,
    ]

    for (const re of pairPatterns) {
      re.lastIndex = 0
      let m
      while ((m = re.exec(blob))) {
        // pattern 0: [slug, urn] ; pattern 1: [urn, slug]
        const ident = re === pairPatterns[0] ? m[1] : m[2]
        const urnRaw = re === pairPatterns[0] ? m[2] : m[1]
        if (slugsMatch(ident, slug)) {
          const id = parseUrnId(urnRaw)
          if (id) return id
        }
      }
    }

    // Ventana alrededor de cada aparición del slug en el HTML.
    const lower = blob.toLowerCase()
    const needle = normalizeSlug(slug)
    let from = 0
    while (from < lower.length) {
      const idx = lower.indexOf(needle, from)
      if (idx < 0) break
      const slice = blob.slice(Math.max(0, idx - 1800), Math.min(blob.length, idx + needle.length + 1800))
      const urnMatch = slice.match(/urn:li:fsd_profile:([A-Za-z0-9_-]+)/)
      if (urnMatch) return urnMatch[1]
      // A veces solo viene el id sin prefijo cerca de profileUrn=
      const recip = slice.match(/profileUrn=urn%3Ali%3Afsd_profile%3A([A-Za-z0-9_-]+)/i)
      if (recip) return recip[1]
      from = idx + Math.max(needle.length, 1)
      // Evitar loops eternos en páginas enormes.
      if (from > 0 && idx === from) from += 1
    }
  }

  // Fallback: compose link en el DOM (ya apunta al destinatario correcto).
  const fromLink = extractUrnFromComposeLinks()
  if (fromLink) return fromLink

  return null
}

function extractUrnFromComposeLinks() {
  const link = findBestComposeLink()
  if (!link) return null
  try {
    const url = new URL(link.getAttribute('href'), window.location.origin)
    return parseUrnId(url.searchParams.get('recipient') || url.searchParams.get('profileUrn'))
  } catch {
    return null
  }
}

/** Solo lectura: cómo abrir el chat sin clicks. Ideal desde isolated world. */
function extractAssistTarget() {
  if (isChatOpen() && findComposeBox()) {
    return { method: 'already-open', composeUrl: null }
  }

  const link = findBestComposeLink()
  if (link) {
    const composeUrl = toAbsoluteComposeUrl(link)
    if (composeUrl) return { method: 'link', composeUrl }
  }

  const urn = extractUrnForCurrentProfile()
  if (urn) {
    const composeUrl = buildComposeUrl(urn)
    if (composeUrl) return { method: 'urn', composeUrl, urn }
  }

  return { method: 'none', composeUrl: null }
}

/**
 * Espera a que LinkedIn cargue el perfil y resuelve cómo abrir el chat.
 * Orden: ya abierto → link compose → URN del slug → un solo click Mensaje → More.
 */
async function resolveChatOpen(maxWaitMs = 14000) {
  window.scrollTo(0, 0)
  const start = Date.now()
  let clickedMessage = false
  let openedMore = false

  while (Date.now() - start < maxWaitMs) {
    if (findComposeBox()) {
      return { method: 'already-open', composeUrl: null }
    }

    const target = extractAssistTarget()
    if (target?.composeUrl) {
      return { method: target.method || 'navigate', composeUrl: target.composeUrl }
    }

    // Un solo intento de click (React a veces lo acepta en MAIN world).
    if (!clickedMessage) {
      const btn = findMessageButton()
      if (btn) {
        const href = toAbsoluteComposeUrl(btn)
        if (href) return { method: 'navigate', composeUrl: href }
        simulateClick(btn)
        clickedMessage = true
        for (let attempt = 0; attempt < 12; attempt += 1) {
          if (findComposeBox()) return { method: 'overlay', composeUrl: null }
          const late = extractAssistTarget()
          if (late?.composeUrl) {
            return { method: late.method || 'navigate-after-click', composeUrl: late.composeUrl }
          }
          await sleep(250)
        }
      }
    }

    if (!openedMore) {
      const more = findMoreActionsButton()
      if (more) {
        simulateClick(more)
        openedMore = true
        await sleep(500)
        const inMenu = findMessageInOpenDropdown()
        if (inMenu) {
          const href = toAbsoluteComposeUrl(inMenu)
          if (href) return { method: 'navigate', composeUrl: href }
          simulateClick(inMenu)
          for (let attempt = 0; attempt < 10; attempt += 1) {
            if (findComposeBox()) return { method: 'overlay-menu', composeUrl: null }
            await sleep(250)
          }
        }
      }
    }

    await sleep(400)
  }

  const late = extractAssistTarget()
  if (late?.composeUrl) {
    return { method: late.method || 'built', composeUrl: late.composeUrl }
  }

  return { method: 'failed', composeUrl: null }
}

function pasteInto(el, text) {
  try {
    el.focus()
    el.click()

    try {
      document.execCommand?.('selectAll', false, null)
      document.execCommand?.('delete', false, null)
    } catch {
      /* ignore */
    }

    if (document.execCommand?.('insertText', false, text)) {
      el.dispatchEvent(
        new InputEvent('input', {
          bubbles: true,
          cancelable: true,
          inputType: 'insertText',
          data: text,
        }),
      )
      if ((el.innerText || el.textContent || '').trim().length > 0) return true
    }

    try {
      const dt = new DataTransfer()
      dt.setData('text/plain', text)
      el.dispatchEvent(
        new ClipboardEvent('paste', {
          bubbles: true,
          cancelable: true,
          clipboardData: dt,
        }),
      )
      if ((el.innerText || el.textContent || '').trim().length > 0) return true
    } catch {
      /* ignore */
    }

    try {
      el.dispatchEvent(
        new InputEvent('beforeinput', {
          bubbles: true,
          cancelable: true,
          inputType: 'insertText',
          data: text,
        }),
      )
    } catch {
      /* ignore */
    }

    // LinkedIn a menudo usa un <p> interno.
    const p = el.querySelector('p')
    if (p) {
      p.textContent = text
    } else {
      el.textContent = text
    }
    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
    return (el.innerText || el.textContent || '').trim().length > 0
  } catch {
    return false
  }
}

async function pasteComposerMessage(text) {
  const message = String(text || '').trim()
  if (!message) return false

  for (let attempt = 0; attempt < 40; attempt += 1) {
    const box = findComposeBox()
    if (box && pasteInto(box, message)) return true
    await sleep(250)
  }
  return false
}
