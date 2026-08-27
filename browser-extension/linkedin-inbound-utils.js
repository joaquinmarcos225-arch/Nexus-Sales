/**
 * Utilidades DOM para detectar mensajes inbound en LinkedIn Messaging.
 * LI-SAFE / LI-IN: solo DOM. Voyager queda disponible pero el content script no lo llama.
 */
window.__NEXUS_LI_INBOUND__ = {
  extractPartnerSlug,
  extractLatestInboundMessage,
  extractLatestInboundIfTheySpokeLast,
  extractLatestThreadMessageForWatch,
  extractLatestInboundByPartnerName,
  extractOpenThreadParticipantName,
  scanConversationPreviews,
  findConversationPreviewByName,
  fetchConversationPreviewsViaApi,
  fingerprintMessage,
  isNoiseMessage,
  isOutboundBubble,
  normalizeLiSlug,
  slugsMatch,
  normalizePersonName,
  namesLooselyMatch,
  looksLikeOutboundSnippet,
  collectInboundDomDiag,
}

const NOISE_PATTERNS = [
  /^invitaci[oó]n/i,
  /^connection/i,
  /^mensaje eliminado/i,
  /^message deleted/i,
  /^archiv/i,
  /^unread/i,
  /^\d{1,2}\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)/i,
  /^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)/i,
  /^linkedin\s+member/i,
  /^miembro de linkedin/i,
]

function decodeSlug(raw) {
  try {
    return decodeURIComponent(String(raw || '').trim()).toLowerCase()
  } catch {
    return String(raw || '').trim().toLowerCase()
  }
}

/** Slug comparable: decode + lower + sin acentos (mia-álvarez ≡ mia-alvarez). */
function normalizeLiSlug(raw) {
  let s = decodeSlug(raw)
  if (!s) return ''
  try {
    s = s.normalize('NFD').replace(/\p{M}/gu, '')
  } catch {
    /* ignore */
  }
  return s
}

function slugsMatch(a, b) {
  const x = normalizeLiSlug(a)
  const y = normalizeLiSlug(b)
  return Boolean(x && y && x === y)
}

function normalizePersonName(raw) {
  let s = String(raw || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  try {
    s = s.normalize('NFD').replace(/\p{M}/gu, '')
  } catch {
    /* ignore */
  }
  s = s.split('·')[0].split('|')[0].trim()
  return s
}

function namesLooselyMatch(a, b) {
  const x = normalizePersonName(a)
  const y = normalizePersonName(b)
  if (!x || !y || x.length < 3 || y.length < 3) return false
  if (x === y) return true
  if (x.includes(y) || y.includes(x)) return true
  const ax = x.split(/\s+/).filter(Boolean)
  const ay = y.split(/\s+/).filter(Boolean)
  if (ax.length >= 2 && ay.length >= 2) {
    const setY = new Set(ay)
    const overlap = ax.filter((p) => setY.has(p))
    if (overlap.length >= 2) return true
  }
  return false
}

/** Snippet de lista tipo "You: …" / "Tú: …" = último mensaje nuestro, no inbound. */
function looksLikeOutboundSnippet(text) {
  const t = String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
  return /^(you|tú|tu|vos|yo)\s*[:：]/i.test(t)
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
    '.msg-overlay-bubble-header a[href*="/in/"]',
    'a[href*="/in/"][data-control-name]',
    '.msg-conversation-listitem--active a[href*="/in/"]',
    '.msg-conversations-container__convo-item--active a[href*="/in/"]',
    'header.msg-overlay-bubble-header a[href*="/in/"]',
    'header a[href*="/in/"]',
    '.msg-title-bar a[href*="/in/"]',
    '.msg-entity-lockup a[href*="/in/"]',
  ]
  for (const sel of selectors) {
    const link = document.querySelector(sel)
    const slug = slugFromHref(link?.getAttribute('href'))
    if (slug) return slug
  }
  const threadRoot =
    document.querySelector('.msg-thread') ||
    document.querySelector('.msg-overlay-conversation-bubble') ||
    document.querySelector('.msg-s-message-list-container')
  if (threadRoot) {
    const link = threadRoot.querySelector('a[href*="/in/"]')
    const slug = slugFromHref(link?.getAttribute('href'))
    if (slug) return slug
  }
  // No usar /messaging/thread/{urn}: no es vanity slug y rompe resolve-linkedin.
  return null
}

function nodeText(el) {
  return (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim()
}

function isNoiseMessage(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  if (t.length < 2) return true
  if (t.length <= 4 && !/[a-záéíóúñ]/i.test(t)) return true
  return NOISE_PATTERNS.some((rx) => rx.test(t))
}

function isOutboundBubble(el) {
  if (!el) return false
  // Inbound explícito gana (evita falsos "vos/you" en aria-labels).
  if (
    el.closest('.msg-s-message-group--other') ||
    el.closest('.msg-s-event-listitem--other') ||
    el.classList?.contains?.('msg-s-event-listitem--other') ||
    el.closest('[class*="message-group--other"]')
  ) {
    return false
  }
  if (
    el.closest('.msg-s-message-group--self') ||
    el.closest('.msg-s-event-listitem--me') ||
    el.classList?.contains?.('msg-s-event-listitem--me') ||
    el.closest('[class*="message-group--self"]')
  ) {
    return true
  }
  const cls = (el.className || '').toString().toLowerCase()
  if (cls.includes('msg-s-event-listitem--other') || cls.includes('message-group--other')) {
    return false
  }
  if (
    cls.includes('msg-s-event-listitem--me') ||
    cls.includes('msg-s-message-group--self') ||
    cls.includes('message-group--self')
  ) {
    return true
  }
  // Layout: burbujas propias suelen alinearse a la derecha.
  try {
    const style = window.getComputedStyle(el)
    if (style?.justifyContent === 'flex-end' || style?.alignItems === 'flex-end') {
      return true
    }
    const parent = el.parentElement
    if (parent) {
      const ps = window.getComputedStyle(parent)
      if (ps?.justifyContent === 'flex-end') return true
    }
  } catch {
    /* ignore */
  }
  const label = (
    el.getAttribute('aria-label') ||
    el.getAttribute('data-sender') ||
    ''
  ).toLowerCase()
  // Solo señales claras de "enviado por mí", no cualquier "you" suelto.
  if (
    /\b(you sent|enviaste|sent by you|vos enviaste|tú enviaste)\b/i.test(label) ||
    label === 'you' ||
    label === 'vos' ||
    label === 'tú' ||
    label === 'tu'
  ) {
    return true
  }
  return false
}

function extractBubbleBody(item) {
  return (
    nodeText(item.querySelector('[data-event-urn] p')) ||
    nodeText(item.querySelector('.msg-s-event-listitem__body')) ||
    nodeText(item.querySelector('.msg-s-event__content')) ||
    nodeText(item.querySelector('.msg-s-event-listitem__message-bubble')) ||
    nodeText(item.querySelector('.msg-s-message-listitem__message-bubble')) ||
    nodeText(item.querySelector('.msg-s-event-listitem__body p')) ||
    nodeText(item.querySelector('.break-words')) ||
    nodeText(item.querySelector('p')) ||
    nodeText(item)
  )
}

function messageEventNodes() {
  const sels = [
    '.msg-s-message-list__event',
    'li.msg-s-message-list__event',
    '.msg-s-event-listitem',
    '.msg-s-message-list li',
    '[data-event-urn]',
    '.msg-s-message-group',
  ]
  const out = []
  const seen = new Set()
  for (const sel of sels) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue
      // Preferir el event wrapper si data-event-urn es hijo.
      const wrap = el.closest?.('.msg-s-message-list__event') || el
      if (seen.has(wrap)) continue
      seen.add(wrap)
      out.push(wrap)
    }
  }
  return out
}

function extractMessageGroupSenderName(item) {
  return (
    nodeText(item.querySelector('.msg-s-message-group__name')) ||
    nodeText(item.querySelector('.msg-s-message-group__profile-link')) ||
    nodeText(item.querySelector('[data-control-name="view_profile"]')) ||
    nodeText(item.querySelector('a[href*="/in/"]')) ||
    ''
  )
}

function extractLatestInboundMessage() {
  const containers = messageEventNodes()
  for (let i = containers.length - 1; i >= 0; i -= 1) {
    const item = containers[i]
    if (isOutboundBubble(item)) continue
    const body = extractBubbleBody(item)
    if (body && !isNoiseMessage(body) && !looksLikeOutboundSnippet(body)) {
      return body.slice(0, 4000)
    }
  }
  return null
}

/** Solo si el ÚLTIMO mensaje real es inbound (ellos hablaron últimos). Evita eco de nuestro outbound. */
function extractLatestInboundIfTheySpokeLast() {
  const containers = messageEventNodes()
  if (!containers.length) return null
  for (let i = containers.length - 1; i >= 0; i -= 1) {
    const item = containers[i]
    const body = extractBubbleBody(item)
    if (!body || isNoiseMessage(body)) continue
    if (looksLikeOutboundSnippet(body)) return null
    if (isOutboundBubble(item)) return null
    return body.slice(0, 4000)
  }
  return null
}

/**
 * Con watch activo: último texto real del hilo que NO es eco de nuestro outbound.
 * Más agresivo (backend también filtra eco).
 */
function extractLatestThreadMessageForWatch() {
  const containers = messageEventNodes()
  for (let i = containers.length - 1; i >= 0; i -= 1) {
    const item = containers[i]
    const body = extractBubbleBody(item)
    if (!body || isNoiseMessage(body)) continue
    if (looksLikeOutboundSnippet(body)) continue
    if (isOutboundBubble(item)) continue
    return body.slice(0, 4000)
  }
  return null
}

/**
 * Patrón linvo/LinkedIn actual: comparar nombre del grupo de mensaje vs nombre del partner.
 * Si el sender del grupo matchea al prospecto → inbound.
 */
function extractLatestInboundByPartnerName(partnerName) {
  const want = normalizePersonName(partnerName)
  if (!want || want.length < 2) return null
  const containers = messageEventNodes()
  for (let i = containers.length - 1; i >= 0; i -= 1) {
    const item = containers[i]
    const body = extractBubbleBody(item)
    if (!body || isNoiseMessage(body) || looksLikeOutboundSnippet(body)) continue
    if (isOutboundBubble(item)) continue
    const sender = extractMessageGroupSenderName(item)
    if (sender && namesLooselyMatch(sender, want)) {
      return body.slice(0, 4000)
    }
    // Sin nombre en el grupo (agrupados): si no es outbound claro, aceptar con watch.
    if (!sender) {
      return body.slice(0, 4000)
    }
  }
  return null
}

function extractOpenThreadParticipantName() {
  const sels = [
    '.msg-thread__link-to-profile h2',
    'a.msg-thread__link-to-profile',
    '[data-control-name="topcard"] h2',
    '.msg-overlay-bubble-header__title',
    '.msg-entity-lockup__entity-title',
    '.msg-title-bar h2',
    'h2.msg-entity-lockup__entity-title',
    '.msg-thread h2',
    '.msg-overlay-conversation-bubble-header h2',
    'header h2',
  ]
  for (const sel of sels) {
    const t = nodeText(document.querySelector(sel))
    if (t && t.length >= 2) return t
  }
  return ''
}

/** Diagnóstico DOM para saber por qué no hay candidatos. */
function collectInboundDomDiag() {
  const events = messageEventNodes()
  const listItems = document.querySelectorAll(
    '.msg-conversation-listitem, .msg-conversations-container__convo-item, li.msg-conversation-card',
  ).length
  const hasList = Boolean(
    document.querySelector('.msg-s-message-list, .msg-conversations-container, .msg-overlay-list-bubble'),
  )
  const sample = []
  for (let i = Math.max(0, events.length - 3); i < events.length; i += 1) {
    const el = events[i]
    sample.push({
      outbound: isOutboundBubble(el),
      sender: extractMessageGroupSenderName(el).slice(0, 80),
      body: extractBubbleBody(el).slice(0, 120),
    })
  }
  return {
    path: String(location.pathname || ''),
    eventNodes: events.length,
    listItems,
    hasList,
    partnerName: extractOpenThreadParticipantName().slice(0, 80),
    partnerSlug: String(extractPartnerSlug() || ''),
    sample,
  }
}

function scanConversationPreviews() {
  const items = [
    ...document.querySelectorAll(
      [
        '.msg-conversation-listitem',
        '.msg-conversations-container__convo-item',
        'li.msg-conversation-card',
        '.msg-overlay-list-bubble__conversations-list .msg-conversation-listitem',
        '.msg-overlay-list-bubble li.msg-conversation-listitem',
        '.msg-overlay-conversation-bubble',
      ].join(', '),
    ),
  ]
  const out = []
  const seenKey = new Set()
  for (const item of items) {
    const link =
      item.querySelector('a[href*="/in/"]') ||
      item.querySelector('a.msg-overlay-bubble-header__profile-link')
    const slug = slugFromHref(link?.getAttribute('href'))
    const participantName =
      nodeText(item.querySelector('.msg-conversation-listitem__participant-names')) ||
      nodeText(item.querySelector('.msg-conversation-card__participant-names')) ||
      nodeText(item.querySelector('.msg-conversation-card__row h3')) ||
      nodeText(item.querySelector('h3')) ||
      nodeText(item.querySelector('.msg-entity-lockup__entity-title')) ||
      ''
    const key = slug || `name:${normalizePersonName(participantName)}`
    if (!key || key === 'name:' || seenKey.has(key)) continue
    seenKey.add(key)
    const unread =
      item.classList.contains('msg-conversation-listitem--unread') ||
      item.querySelector(
        '.msg-conversation-card__unread-count, .notification-badge, .msg-conversation-card__unread-count-container, .msg-conversation-listitem__unread-count',
      ) != null
    const preview =
      nodeText(item.querySelector('.msg-conversation-listitem__message-snippet')) ||
      nodeText(item.querySelector('.msg-conversation-card__snippet')) ||
      nodeText(item.querySelector('.msg-conversation-listitem__message-snippet-body')) ||
      nodeText(item.querySelector('.msg-overlay-conversation-bubble__content')) ||
      ''
    if (!preview || isNoiseMessage(preview)) continue
    const row = {
      slug: slug || null,
      participantName: participantName || null,
      text: preview.slice(0, 4000),
      unread: Boolean(unread),
    }
    if (unread) out.unshift(row)
    else out.push(row)
  }
  return out
}

/**
 * Cuando LinkedIn no pone /in/ en la lista: matchear por nombre del watch.
 * Solo válido con watch armado (el caller pasa el slug vigilado).
 */
function findConversationPreviewByName(prospectName) {
  const want = normalizePersonName(prospectName)
  if (!want || want.length < 3) return null
  for (const preview of scanConversationPreviews()) {
    if (!preview.participantName) continue
    if (!namesLooselyMatch(preview.participantName, want)) continue
    if (looksLikeOutboundSnippet(preview.text)) continue
    return preview
  }
  return null
}

function fingerprintMessage(slug, text) {
  const base = `${slug || 'unknown'}:${String(text || '').trim().slice(0, 500)}`
  let hash = 0
  for (let i = 0; i < base.length; i += 1) {
    hash = (hash << 5) - hash + base.charCodeAt(i)
    hash |= 0
  }
  return `ext:${Math.abs(hash)}`
}

function csrfToken() {
  const raw = document.cookie
    .split(';')
    .map((s) => s.trim())
    .find((s) => s.startsWith('JSESSIONID='))
  if (!raw) return ''
  try {
    return decodeURIComponent(raw.slice('JSESSIONID='.length)).replace(/"/g, '')
  } catch {
    return raw.slice('JSESSIONID='.length).replace(/"/g, '')
  }
}

function voyagerHeaders(token) {
  return {
    accept: 'application/vnd.linkedin.normalized+json+2.1',
    'csrf-token': token,
    'x-restli-protocol-version': '2.0.0',
    'x-li-lang': document.documentElement?.lang || 'es_ES',
  }
}

let cachedSelfSlug = null
let selfSlugFetchedAt = 0

async function resolveSelfSlug(token) {
  const now = Date.now()
  if (cachedSelfSlug && now - selfSlugFetchedAt < 10 * 60_000) return cachedSelfSlug
  try {
    const res = await fetch('https://www.linkedin.com/voyager/api/me', {
      method: 'GET',
      credentials: 'include',
      headers: voyagerHeaders(token),
    })
    if (res.ok) {
      const data = await res.json()
      const included = Array.isArray(data?.included) ? data.included : []
      const mini =
        included.find((e) => e?.publicIdentifier && String(e.$type || '').includes('MiniProfile')) ||
        included.find((e) => e?.publicIdentifier) ||
        data?.data
      const pub = mini?.publicIdentifier || data?.data?.publicIdentifier
      if (pub) {
        cachedSelfSlug = decodeSlug(pub)
        selfSlugFetchedAt = now
        return cachedSelfSlug
      }
    }
  } catch {
    /* ignore */
  }
  const href = document.querySelector('.global-nav__me a[href*="/in/"]')?.getAttribute('href')
  const fromDom = slugFromHref(href)
  if (fromDom) {
    cachedSelfSlug = fromDom
    selfSlugFetchedAt = now
  }
  return cachedSelfSlug
}

/**
 * Conversaciones vía API Voyager (LEGACY — no usar bajo LI-SAFE / LI-IN).
 * El content script LI-IN no llama esto.
 */
async function fetchConversationPreviewsViaApi(opts = {}) {
  // LI-SAFE: hard no-op aunque alguien lo invoque.
  if (typeof window !== 'undefined' && window.__NEXUS_LI_INBOUND_GEN__?.includes?.('li-in')) {
    return []
  }
  const unreadOnly = opts.unreadOnly !== false
  const token = csrfToken()
  if (!token) return []

  const selfSlug = await resolveSelfSlug(token)
  const headers = voyagerHeaders(token)
  const endpoints = [
    'https://www.linkedin.com/voyager/api/messaging/conversations?keyVersion=LEGACY_INBOX&q=searchFilter&filters=List(NOT_MUTED)&count=40',
    'https://www.linkedin.com/voyager/api/messaging/conversations?q=searchFilter&filters=List()&count=40',
    'https://www.linkedin.com/voyager/api/messaging/conversations?count=40',
    'https://www.linkedin.com/voyager/api/voyagerMessagingDashMessengerConversations?count=40&q=searchFilter&filters=List()',
  ]

  for (const url of endpoints) {
    try {
      const res = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        headers,
      })
      if (!res.ok) continue
      const data = await res.json()
      let parsed = parseVoyagerConversations(data, selfSlug)
      if (!parsed.length) parsed = parseVoyagerConversationsLoose(data, selfSlug)
      if (!parsed.length) continue
      if (unreadOnly) {
        const unread = parsed.filter((p) => p.unread)
        // LinkedIn a menudo no expone unreadCount; si no hay unread, usar recientes con texto inbound.
        if (unread.length) return unread
        return parsed.slice(0, 6)
      }
      // unreadOnly:false → recientes (badge/título); tope para no inundar.
      return parsed.slice(0, 10)
    } catch {
      /* next endpoint */
    }
  }
  return []
}

function parseVoyagerConversations(data, selfSlug) {
  const included = Array.isArray(data?.included) ? data.included : []
  const byUrn = new Map()
  for (const ent of included) {
    if (!ent || typeof ent !== 'object') continue
    const urn = ent.entityUrn
    if (urn) {
      byUrn.set(String(urn), ent)
      byUrn.set(`*${urn}`, ent)
    }
  }

  function resolve(ref) {
    if (!ref) return null
    if (typeof ref === 'object') return ref
    const key = String(ref)
    return byUrn.get(key) || byUrn.get(key.startsWith('*') ? key.slice(1) : `*${key}`) || null
  }

  function slugFromParticipant(partRef) {
    const part = resolve(partRef)
    if (!part) return null
    const mini =
      resolve(part['*miniProfile'] || part.miniProfile || part['*profile'] || part.profile) || part
    const pub =
      mini?.publicIdentifier || part.publicIdentifier || mini?.vanityName || part.vanityName
    if (!pub || String(pub).startsWith('UNKNOWN')) return null
    const slug = decodeSlug(pub)
    if (selfSlug && slug === selfSlug) return null
    return slug
  }

  function textFromMessage(msgRef) {
    const msg = resolve(msgRef)
    if (!msg || typeof msg !== 'object') {
      if (typeof msgRef === 'string' && msgRef.length > 2 && !msgRef.includes('urn:')) {
        return msgRef.replace(/\s+/g, ' ').trim()
      }
      return ''
    }
    const from = resolve(msg['*from'] || msg.from || msg.actor)
    const fromMini = resolve(from?.['*miniProfile'] || from?.miniProfile)
    const fromPub = from?.publicIdentifier || fromMini?.publicIdentifier
    if (selfSlug && fromPub && decodeSlug(fromPub) === selfSlug) return ''
    const raw =
      msg?.eventContent?.attributedBody?.text ||
      msg?.eventContent?.body?.text ||
      msg?.eventContent?.comment?.value ||
      msg?.attributedBody?.text ||
      msg?.body?.text ||
      msg?.snippet ||
      msg?.previewContent?.text ||
      ''
    return String(raw || '')
      .replace(/\s+/g, ' ')
      .trim()
  }

  const rawElements =
    data?.data?.['*elements'] ||
    data?.data?.elements ||
    data?.elements ||
    data?.data?.messengerConversationsByCategoryQuery?.['*elements'] ||
    []
  const list = Array.isArray(rawElements) ? rawElements : []
  const out = []
  const seen = new Set()

  for (const el of list) {
    const conv = resolve(el) || (typeof el === 'object' ? el : null)
    if (!conv) continue

    const unread =
      Number(conv.unreadCount || conv.unreadMessageCount || 0) > 0 ||
      Boolean(conv.read === false)

    let slug = null
    const participants =
      conv['*participants'] ||
      conv.participants ||
      conv['*conversationParticipants'] ||
      conv.conversationParticipants ||
      []
    for (const p of Array.isArray(participants) ? participants : []) {
      slug = slugFromParticipant(p)
      if (slug) break
    }
    if (!slug) continue

    const eventRefs = conv['*events'] || conv.events || conv['*messages'] || conv.messages || []
    const events = Array.isArray(eventRefs) ? eventRefs : []
    let text = ''
    let lastFromSelf = null

    // Elegir el evento más reciente (timestamp) o events[0] (Voyager suele poner el último primero).
    let newestRef = events[0] || null
    let newestTs = -1
    for (const ref of events) {
      const msg = resolve(ref)
      if (!msg || typeof msg !== 'object') continue
      const ts = Number(msg.createdAt || msg.deliveredAt || msg.lastActivityAt || 0)
      if (ts > newestTs) {
        newestTs = ts
        newestRef = ref
      }
    }
    if (newestRef != null) {
      const msg = resolve(newestRef)
      const from = resolve(msg?.['*from'] || msg?.from || msg?.actor)
      const fromMini = resolve(from?.['*miniProfile'] || from?.miniProfile)
      const fromPub = from?.publicIdentifier || fromMini?.publicIdentifier
      if (selfSlug && fromPub && decodeSlug(fromPub) === selfSlug) {
        lastFromSelf = true
      } else if (fromPub) {
        lastFromSelf = false
      }
      text = textFromMessage(newestRef)
    }
    // Si el último es nuestro, no reportar esta conversación (ellos no hablaron últimos).
    if (lastFromSelf === true) continue

    if (!text) {
      text =
        textFromMessage(conv.lastActivity) ||
        textFromMessage(conv['*lastMessage'] || conv.lastMessage) ||
        ''
    }
    if (!text) {
      const sn = conv.snippet || conv.preview || conv.shortPreviewText || ''
      text = typeof sn === 'string' ? sn : sn?.text || ''
      text = String(text || '')
        .replace(/\s+/g, ' ')
        .trim()
    }
    if (!text || isNoiseMessage(text)) continue
    if (seen.has(slug)) continue
    seen.add(slug)
    out.push({ slug, text: text.slice(0, 4000), unread })
  }

  return out
}

function parseVoyagerConversationsLoose(data, selfSlug) {
  const included = Array.isArray(data?.included) ? data.included : []
  const byUrn = new Map()
  for (const ent of included) {
    if (!ent?.entityUrn) continue
    byUrn.set(String(ent.entityUrn), ent)
    byUrn.set(`*${ent.entityUrn}`, ent)
  }

  const out = []
  const seen = new Set()

  for (const ent of included) {
    if (!ent || typeof ent !== 'object') continue
    const type = String(ent.$type || ent.entityUrn || '')
    const looksConv =
      /Conversation/i.test(type) ||
      String(ent.entityUrn || '').includes('msg_conversation') ||
      String(ent.entityUrn || '').includes('MessengerConversation')
    if (!looksConv) continue

    const unread = Number(ent.unreadCount || ent.unreadMessageCount || 0) > 0
    const participants = ent['*participants'] || ent.participants || []
    let slug = null
    for (const p of Array.isArray(participants) ? participants : []) {
      const part = typeof p === 'string' ? byUrn.get(p) || byUrn.get(p.replace(/^\*/, '')) : p
      if (!part) continue
      const miniRef = part['*miniProfile'] || part.miniProfile
      const mini =
        typeof miniRef === 'string'
          ? byUrn.get(miniRef) || byUrn.get(miniRef.replace(/^\*/, ''))
          : miniRef
      const pub = mini?.publicIdentifier || part.publicIdentifier
      if (pub && !String(pub).startsWith('UNKNOWN')) {
        const s = decodeSlug(pub)
        if (selfSlug && s === selfSlug) continue
        slug = s
        break
      }
    }
    if (!slug) continue

    let text = ent.snippet || ent.preview || ent.shortPreviewText || ''
    if (typeof text === 'object') text = text?.text || ''
    text = String(text || '')
      .replace(/\s+/g, ' ')
      .trim()
    if (!text || isNoiseMessage(text)) continue
    if (seen.has(slug)) continue
    seen.add(slug)
    out.push({ slug, text: text.slice(0, 4000), unread })
  }
  return out
}
