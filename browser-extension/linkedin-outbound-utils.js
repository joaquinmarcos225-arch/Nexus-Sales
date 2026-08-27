/**
 * Utilidades DOM para detectar envío outbound en LinkedIn Messaging.
 */
window.__NEXUS_LI_OUTBOUND__ = {
  extractPartnerSlug,
  detectOutboundSent,
  lastOutboundBody,
  composerHasContent,
  composerMatchesPrefix,
}

function decodeSlug(raw) {
  try {
    return decodeURIComponent(String(raw || '').trim()).toLowerCase()
  } catch {
    return String(raw || '').trim().toLowerCase()
  }
}

function slugFromHref(href) {
  if (!href) return null
  try {
    const path = new URL(href, 'https://www.linkedin.com').pathname.toLowerCase()
    const idx = path.indexOf('/in/')
    if (idx < 0) return null
    const slug = path.slice(idx + 4).split('/')[0].trim()
    return slug || null
  } catch {
    return null
  }
}

function extractPartnerSlug() {
  const selectors = [
    'a.msg-thread__link-to-profile',
    'a.msg-overlay-bubble-header__profile-link',
    'a.msg-overlay-conversation-bubble__profile-link',
    '.msg-overlay-bubble-header a[href*="/in/"]',
    '.msg-thread-meta__profile-link',
    'a[href*="/in/"][data-control-name]',
    '.msg-conversation-listitem--active a[href*="/in/"]',
    'header a[href*="/in/"]',
  ]
  for (const sel of selectors) {
    const links = document.querySelectorAll(sel)
    for (const link of links) {
      const slug = slugFromHref(link?.getAttribute('href'))
      if (slug && !/^\d+-/.test(slug)) return slug
    }
  }
  // NO usar /messaging/thread/{uuid} como slug de perfil.
  return null
}

function nodeText(el) {
  return (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim()
}

function normalizeText(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

function isOutboundBubble(el) {
  if (!el) return false
  const cls = (el.className || '').toString().toLowerCase()
  if (cls.includes('msg-s-event-listitem--other')) return false
  if (cls.includes('msg-s-event-listitem--me')) return true
  if (cls.includes('msg-s-message-group--self')) return true
  if (el.closest('.msg-s-message-group--self')) return true
  if (el.closest('.msg-s-event-listitem--other')) return false
  // Heurística visual: mensajes propios suelen alinearse a la derecha.
  try {
    const style = window.getComputedStyle(el)
    if (style.justifyContent === 'flex-end' || style.alignSelf === 'flex-end') return true
  } catch {
    /* ignore */
  }
  const label = (
    el.getAttribute('aria-label') ||
    el.getAttribute('data-sender') ||
    ''
  ).toLowerCase()
  if (label.includes('you') || label.includes('vos') || label.includes('tú') || label.includes('tu ')) {
    return true
  }
  return false
}

function extractBubbleBody(item) {
  return (
    nodeText(item.querySelector('.msg-s-event-listitem__body')) ||
    nodeText(item.querySelector('.msg-s-event__content')) ||
    nodeText(item.querySelector('p')) ||
    nodeText(item)
  )
}

function textsMatch(expectedPrefix, actual) {
  const expected = normalizeText(expectedPrefix)
  const actualNorm = normalizeText(actual)
  if (!expected || !actualNorm) return false
  const head = expected.slice(0, Math.min(80, expected.length))
  const tail = actualNorm.slice(0, Math.min(80, actualNorm.length))
  return actualNorm.includes(head) || expected.includes(tail) || head === tail
}

function messageListItems() {
  return [
    ...document.querySelectorAll(
      [
        '.msg-s-message-list__event',
        '.msg-s-event-listitem',
        'li.msg-s-message-list__event',
        '.msg-s-message-list-content li',
        '.msg-overlay-conversation-bubble__message-list li',
      ].join(', '),
    ),
  ]
}

function detectOutboundSent(expectedPrefix) {
  const containers = messageListItems()
  // Primero: burbujas claramente propias.
  for (let i = containers.length - 1; i >= 0; i -= 1) {
    const item = containers[i]
    if (!isOutboundBubble(item)) continue
    const body = extractBubbleBody(item)
    if (body && textsMatch(expectedPrefix, body)) return true
  }
  // Fallback: cualquier ítem reciente del hilo cuyo texto coincida (LinkedIn cambia clases).
  const expected = normalizeText(expectedPrefix)
  if (expected.length < 12) return false
  const head = expected.slice(0, Math.min(60, expected.length))
  for (let i = containers.length - 1; i >= Math.max(0, containers.length - 12); i -= 1) {
    const body = normalizeText(extractBubbleBody(containers[i]))
    if (body.includes(head)) return true
  }
  // Último recurso: texto visible en el panel de mensajes.
  const panel = document.querySelector(
    '.msg-s-message-list, .msg-overlay-conversation-bubble__content-wrapper, .msg-thread',
  )
  const panelText = normalizeText(panel)
  return Boolean(panelText && panelText.includes(head))
}

/** Último mensaje saliente del hilo. */
function lastOutboundBody() {
  const containers = messageListItems()
  for (let i = containers.length - 1; i >= 0; i -= 1) {
    const item = containers[i]
    if (!isOutboundBubble(item)) continue
    const body = extractBubbleBody(item)
    if (body && body.length >= 6) return body
  }
  // Fallback: último ítem con texto largo.
  for (let i = containers.length - 1; i >= 0; i -= 1) {
    const body = extractBubbleBody(containers[i])
    if (body && body.length >= 12) return body
  }
  return ''
}

function findComposer() {
  const selectors = [
    'div.msg-form__contenteditable[contenteditable="true"]',
    '.msg-form div[contenteditable="true"]',
    'div.msg-overlay div[contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]',
  ]
  for (const sel of selectors) {
    const el = document.querySelector(sel)
    if (el) return el
  }
  return null
}

function composerHasContent() {
  const el = findComposer()
  const text = nodeText(el)
  return text.length >= 2
}

function composerMatchesPrefix(expectedPrefix) {
  const el = findComposer()
  const text = nodeText(el)
  if (!text) return false
  return textsMatch(expectedPrefix, text)
}
