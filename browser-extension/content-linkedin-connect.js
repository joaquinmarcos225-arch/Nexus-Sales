/**
 * Detecta si un perfil es conexión de 1er grado y lo reporta a Nexus.
 *
 * Señal principal (UI ES/EN actual):
 *  - Contacto: badge "· 1er" / "1st" junto al nombre
 *  - No contacto: badge "· 2º" / "· 3er" / "2nd" / "3rd"
 *
 * Importante: "Enviar mensaje" NO implica 1º grado (Open Profile / InMail).
 * Contactar a menudo está en Más (⋯) o en /preload/custom-invite/?vanityName=…
 */
const CONNECT_POLL_MS = 2500
const CONNECT_SEEN_KEY = 'nexusLiConnectSeen'
const MAX_CONNECT_SEEN = 400
const CONNECT_SEEN_TTL_MS = 45 * 1000
let connectMutationTimer = null

function csrfToken() {
  const raw = document.cookie
    .split(';')
    .map((s) => s.trim())
    .find((s) => s.startsWith('JSESSIONID='))
  if (raw) {
    try {
      return decodeURIComponent(raw.slice('JSESSIONID='.length)).replace(/^"|"$/g, '')
    } catch {
      return raw.slice('JSESSIONID='.length).replace(/^"|"$/g, '')
    }
  }
  return ''
}

/** JSESSIONID suele ser HttpOnly → pedir CSRF al service worker (permiso cookies). */
async function csrfTokenAsync() {
  const local = csrfToken()
  if (local) return local
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: 'NEXUS_GET_LINKEDIN_CSRF' }, (res) => {
        if (chrome.runtime.lastError) {
          resolve('')
          return
        }
        resolve(String(res?.csrf || '').trim())
      })
    } catch {
      resolve('')
    }
  })
}

void initConnectWatcher()

/** Overlay solo cuando SÍ leyó el grado (nunca "NO LEÍ"). */
function showDegreeDebugOverlay(degree) {
  if (!(degree === 1 || degree === 2 || degree === 3)) return
  try {
    const existing = document.getElementById('nexus-degree-debug')
    if (existing) existing.remove()
    const el = document.createElement('div')
    el.id = 'nexus-degree-debug'
    const label = degree === 1 ? '1 (contacto)' : String(degree)
    el.textContent = `Nexus lee: ${label}`
    el.style.cssText = [
      'position:fixed',
      'z-index:2147483647',
      'top:20px',
      'left:50%',
      'transform:translateX(-50%)',
      'background:#0A66C2',
      'color:#fff',
      'padding:14px 22px',
      'border-radius:12px',
      'font:700 18px/1.3 system-ui,sans-serif',
      'box-shadow:0 10px 28px rgba(0,0,0,.35)',
      'pointer-events:none',
    ].join(';')
    document.documentElement.appendChild(el)
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

/**
 * TEST: número de grado 1 | 2 | 3 (badge o Voyager).
 * @returns {Promise<1|2|3|null>}
 */
async function readDegreeNumber(slug) {
  for (let i = 0; i < 15; i++) {
    const badge = readProfileDegreeBadge()
    if (badge === 1 || badge === 2 || badge === 3) return badge
    await new Promise((r) => setTimeout(r, 200))
  }
  const n = await fetchDegreeNumberViaVoyager(String(slug || '').trim())
  if (n === 1 || n === 2 || n === 3) return n
  return null
}

/** Igual que fetchDistanceViaVoyager pero devuelve 1|2|3. */
async function fetchDegreeNumberViaVoyager(slug) {
  const token = await csrfTokenAsync()
  if (!token || !slug) return null
  const uniq = slugIdentityVariants(slug)
  const headers = {
    accept: 'application/vnd.linkedin.normalized+json+2.1',
    'csrf-token': token,
    'x-restli-protocol-version': '2.0.0',
    'x-li-lang': document.documentElement?.lang || 'es_ES',
  }
  for (const identity of uniq) {
    try {
      const url = `https://www.linkedin.com/voyager/api/identity/profiles/${encodeURIComponent(identity)}/networkinfo`
      const res = await fetch(url, { method: 'GET', credentials: 'include', headers })
      if (!res.ok) continue
      const data = await res.json()
      const dist = data?.data?.distance || data?.distance
      const n = distanceTokenToNumber(dist?.value ?? dist)
      if (n) return n
    } catch {
      /* next */
    }
  }
  const decorations = [
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-128',
    'com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-16',
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardCore-16',
  ]
  for (const identity of uniq) {
    for (const decorationId of decorations) {
      try {
        const url =
          `https://www.linkedin.com/voyager/api/identity/dash/profiles` +
          `?q=memberIdentity&memberIdentity=${encodeURIComponent(identity)}` +
          `&decorationId=${encodeURIComponent(decorationId)}`
        const res = await fetch(url, { method: 'GET', credentials: 'include', headers })
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
          if (pub && pub !== want && strip(pub) !== strip(want)) continue
          const raw =
            ent.memberDistance?.value || ent.memberDistance || ent.networkDistance || ent.distance
          const n = distanceTokenToNumber(raw)
          if (n) return n
        }
        const primary = data?.data
        if (primary && typeof primary === 'object' && !Array.isArray(primary)) {
          const raw =
            primary.memberDistance?.value ||
            primary.memberDistance ||
            primary.networkDistance ||
            primary.distance
          const n = distanceTokenToNumber(raw)
          if (n) return n
        }
      } catch {
        /* next */
      }
    }
  }
  return null
}

function initConnectWatcher() {
  if (!window.location.hostname.includes('linkedin.com')) return
  if (window.__NEXUS_LI_CONNECT__) return
  window.__NEXUS_LI_CONNECT__ = true

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'NEXUS_DEBUG_READ_DEGREE') {
      // TEST: leer 1/2/3 y mostrarlo en la página LinkedIn.
      void (async () => {
        const slug = String(message?.profileSlug || currentProfileSlug() || '')
          .trim()
          .toLowerCase()
        const degree = await readDegreeNumber(slug)
        showDegreeDebugOverlay(degree)
        sendResponse({ ok: degree === 1 || degree === 2 || degree === 3, degree })
      })().catch(() => sendResponse({ ok: false, degree: null }))
      return true
    }
    if (message?.type === 'NEXUS_PING_CONNECT') {
      sendResponse({ ok: true })
      return false
    }
    if (message?.type === 'NEXUS_VOYAGER_DISTANCE') {
      // Lee distancia vía API sin navegar al perfil (no roba foco).
      void (async () => {
        const slug = String(message?.profileSlug || '')
          .trim()
          .toLowerCase()
        if (!slug) {
          sendResponse({ ok: false, verdict: null })
          return
        }
        const verdict = await fetchDistanceViaVoyager(slug)
        sendResponse({ ok: Boolean(verdict), verdict })
      })().catch(() => sendResponse({ ok: false, verdict: null }))
      return true
    }
    if (message?.type === 'NEXUS_READ_CONNECTION_VERDICT') {
      // Solo lee el DOM/API — NO reporta al backend (así el SW puede cerrar la pestaña YA).
      // mode: 'invite_sent' → solo confirma aceptación con evidencia fuerte (1er / DISTANCE_1).
      // fast: true → Voyager primero, badge corto (probes en background).
      // quick: true → 1 sola pasada Voyager+badge (evita deadlock/timeout con el SW).
      void (async () => {
        const slug = String(message?.profileSlug || currentProfileSlug() || '')
          .trim()
          .toLowerCase()
        if (!slug) {
          sendResponse({ ok: false, verdict: null })
          return
        }
        const inviteMode = String(message?.mode || '') === 'invite_sent'
        const fast = Boolean(message?.fast) || Boolean(message?.quick)
        const quick = Boolean(message?.quick)
        try {
          window.scrollTo(0, 0)
        } catch {
          /* ignore */
        }
        let verdict = null
        if (inviteMode) {
          verdict = await resolveAcceptanceVerdict(slug)
        } else if (quick) {
          verdict = await fetchDistanceViaVoyager(slug)
          if (!verdict) {
            const degree = readProfileDegreeBadge()
            if (degree === 1) verdict = 'connected'
            else if (degree === 2 || degree === 3) verdict = 'not_connected'
          }
          // CTA Contactar/Pendiente = no 1º (mismo criterio que resolveConnectionVerdict).
          if (!verdict && profileTopCardReady()) {
            if (hasPendingInviteOnTopCard()) verdict = 'not_connected'
            else if (hasVisibleConnectInTopCard() || hasCustomInviteLink()) verdict = 'not_connected'
          }
        } else {
          verdict = await resolveConnectionVerdict(slug, { fast })
        }
        const degree = readProfileDegreeBadge()
        sendResponse({
          ok: Boolean(verdict),
          verdict,
          degree:
            degree === 1 || degree === 2 || degree === 3
              ? degree
              : verdict === 'connected'
                ? 1
                : verdict === 'not_connected'
                  ? 2
                  : null,
        })
      })().catch(() => sendResponse({ ok: false, verdict: null }))
      return true
    }
    if (message?.type === 'NEXUS_FORCE_CONNECTION_CHECK') {
      void forceConnectionCheck(message)
        .then((result) => {
          if (result && typeof result === 'object') {
            sendResponse({ ok: Boolean(result.ok), ...result })
          } else {
            sendResponse({ ok: Boolean(result) })
          }
        })
        .catch(() => sendResponse({ ok: false }))
      return true
    }
    return false
  })

  // Solo intervalo local — no compartir NEXUS_POLL_INBOUND (eso es para mensajes entrantes).
  void pollConnections('init')
  setInterval(() => void pollConnections('interval'), CONNECT_POLL_MS)

  const root = document.body || document.documentElement
  if (root) {
    const observer = new MutationObserver(() => {
      if (connectMutationTimer) return
      connectMutationTimer = window.setTimeout(() => {
        connectMutationTimer = null
        void pollConnections('mutation')
      }, 1000)
    })
    observer.observe(root, { childList: true, subtree: true })
  }
}

function currentProfileSlug() {
  const path = window.location.pathname.toLowerCase()
  const idx = path.indexOf('/in/')
  if (idx < 0) return null
  const slug = path.slice(idx + 4).split('/')[0]
  return slug ? decodeURIComponent(slug) : null
}

function profileTopCardRoots() {
  return [
    ...document.querySelectorAll(
      [
        'main section.artdeco-card',
        'main .pv-top-card',
        '.pv-text-details__left-panel',
        '[data-view-name="profile-top-card"]',
        '[data-view-name="profile-main-top-card"]',
        '.ph5.pb5',
        'section.artdeco-card .ph5',
      ].join(', '),
    ),
  ].slice(0, 4)
}

/**
 * Lee el grado junto al nombre del perfil actual.
 * Señal canónica ES/EN: "· 1er" / "· 2º" / "· 3er" junto al h1.
 * Ojo: º/° no son word-chars → no usar \b después (rompe "· 2º").
 * @returns {1|2|3|null}
 */
function readProfileDegreeBadge() {
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
  const findDegree = (raw, { shortBadge = false } = {}) => {
    const t = String(raw || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    if (!t) return null
    // 2º/3º ganan sobre “1er” suelto en el mismo texto.
    if (hasSecond(t)) return 2
    if (hasThird(t)) return 3
    if (shortBadge && hasFirst(t)) return 1
    if (/[·•]\s*1(?:[\s.]*(?:er|ero|º|°|st|ro))?/i.test(t) || /\b1(?:er)?\s*grado\b/i.test(t)) {
      return 1
    }
    return null
  }

  // CTA Contactar / Pendiente = nunca 1º.
  if (profileTopCardReady()) {
    if (hasPendingInviteOnTopCard()) return 2
    if (hasVisibleConnectInTopCard() || hasCustomInviteLink()) return 2
  }

  for (const sel of [
    '.dist-value',
    '.distance-badge',
    'span.artdeco-entity-lockup__degree',
    '[class*="distance-badge"]',
    '[class*="dist-value"]',
  ]) {
    for (const el of document.querySelectorAll(sel)) {
      const raw = (el.textContent || '').replace(/\s+/g, ' ').trim()
      if (!raw || raw.length > 24) continue
      const d = findDegree(raw, { shortBadge: true })
      if (d) return d
    }
  }

  const nameEl =
    document.querySelector('main h1') ||
    document.querySelector('h1') ||
    document.querySelector('[data-anonymize="person-name"]')
  if (nameEl) {
    const box =
      nameEl.closest('section') ||
      nameEl.closest('[data-view-name="profile-top-card"]') ||
      nameEl.parentElement?.parentElement?.parentElement ||
      nameEl.parentElement
    const near = `${nameEl.textContent || ''} ${nameEl.parentElement?.innerText || ''} ${box?.innerText || ''}`
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 600)
    const d = findDegree(near, { shortBadge: false })
    if (d) return d
  }

  return null
}

function profileTopCardReady() {
  return Boolean(
    document.querySelector('main h1') ||
      document.querySelector('.pv-text-details__left-panel h1') ||
      document.querySelector('[data-anonymize="person-name"]'),
  )
}

function hasPendingInviteOnTopCard() {
  for (const el of profileActionButtons()) {
    const label = buttonLabel(el)
    if (/\b(pending|pendiente)\b/.test(label)) return true
  }
  return false
}

/**
 * Veredicto estricto post-Contactar (invite_sent).
 * @returns {Promise<'connected'|'not_yet'|null>}
 */
async function resolveAcceptanceVerdict(slug) {
  for (let i = 0; i < 16; i++) {
    const degree = readProfileDegreeBadge()
    if (degree === 1) return 'connected'
    if (degree === 2 || degree === 3) return 'not_yet'
    await new Promise((r) => setTimeout(r, 300))
  }
  if (hasPendingInviteOnTopCard()) return 'not_yet'
  if (profileTopCardReady() && (hasVisibleConnectInTopCard() || hasCustomInviteLink())) {
    return 'not_yet'
  }
  const viaApi = await fetchDistanceViaVoyager(slug)
  if (viaApi === 'connected') return 'connected'
  if (viaApi === 'not_connected') return 'not_yet'
  return null
}

/**
 * ¿Es 1º grado?
 * - Badge 1/1er = contacto; 2/2do; 3/3ro = no.
 * - Voyager (dash/profiles) cuando el badge no pinta (tabs background / feed).
 * - Fallback: botones del top card (Contactar vs Mensaje) solo con top card listo.
 * @returns {Promise<'connected'|'not_connected'|null>}
 */
async function resolveConnectionVerdict(slug, opts = {}) {
  const fast = Boolean(opts?.fast)
  const identity = String(slug || '').trim()

  const fromDegreeOrApi = async () => {
    const viaApi = await fetchDistanceViaVoyager(identity)
    if (viaApi === 'not_connected') return viaApi
    if (viaApi === 'connected') {
      // Contactar visible veta Voyager 1º.
      const cta = fromTopCardCtas()
      if (cta) return cta
      return viaApi
    }
    const degree = readProfileDegreeBadge()
    if (degree === 2 || degree === 3) return 'not_connected'
    if (degree === 1) {
      const cta = fromTopCardCtas()
      if (cta) return cta
      return 'connected'
    }
    // scrapeConnectedNearSlug solo si no hay CTA Contactar.
    if (!fromTopCardCtas() && scrapeConnectedNearSlug(identity)) return 'connected'
    return null
  }

  /** Solo si el top card ya pintó: Contactar = no contacto. Mensaje solo NO implica 1º. */
  const fromTopCardCtas = () => {
    if (!profileTopCardReady()) return null
    if (hasPendingInviteOnTopCard()) return 'not_connected'
    if (hasVisibleConnectInTopCard() || hasCustomInviteLink()) return 'not_connected'
    // Open Profile / InMail muestran «Enviar mensaje» sin ser 1º grado — no inventar connected.
    return null
  }

  // Probes en background: Voyager primero (LinkedIn no pinta ·1er en tabs inactive).
  if (fast) {
    const viaApi = await fetchDistanceViaVoyager(identity)
    if (viaApi === 'not_connected') return viaApi
    if (viaApi === 'connected') {
      const cta = fromTopCardCtas()
      if (cta) return cta
      return viaApi
    }
    for (let i = 0; i < 10; i++) {
      const degree = readProfileDegreeBadge()
      if (degree === 2 || degree === 3) return 'not_connected'
      if (degree === 1) {
        const cta = fromTopCardCtas()
        if (cta) return cta
        return 'connected'
      }
      const cta = fromTopCardCtas()
      if (cta) return cta
      await new Promise((r) => setTimeout(r, 200))
    }
    return null
  }

  for (let i = 0; i < 30; i++) {
    const degree = readProfileDegreeBadge()
    if (degree === 2 || degree === 3) return 'not_connected'
    if (degree === 1) {
      const cta = fromTopCardCtas()
      if (cta) return cta
      return 'connected'
    }
    if (i === 4 || i === 12) {
      const viaApi = await fetchDistanceViaVoyager(identity)
      if (viaApi === 'not_connected') return viaApi
      if (viaApi === 'connected') {
        const cta = fromTopCardCtas()
        if (cta) return cta
        return viaApi
      }
    }
    const cta = fromTopCardCtas()
    if (cta) return cta
    await new Promise((r) => setTimeout(r, 250))
  }
  const last = await fromDegreeOrApi()
  if (last) return last
  return fromTopCardCtas()
}

/** Solo evidencia positiva de 1er cerca del slug (nunca not_connected desde HTML). */
function scrapeConnectedNearSlug(slug) {
  const variants = slugIdentityVariants(slug).map((s) => s.toLowerCase())
  if (!variants.length) return false
  let html = ''
  try {
    html = document.documentElement?.innerHTML || ''
  } catch {
    return false
  }
  if (html.length < 500) return false
  const lower = html.toLowerCase()
  for (const v of variants) {
    if (!v || v.length < 3) continue
    let from = 0
    let hits = 0
    while (from < lower.length && hits < 8) {
      const idx = lower.indexOf(v, from)
      if (idx < 0) break
      hits += 1
      // Ventana más chica: menos contaminación de otros perfiles en el mismo blob.
      const slice = html.slice(Math.max(0, idx - 400), idx + v.length + 400)
      if (
        /DISTANCE_1\b/i.test(slice) ||
        /"networkDistance"\s*:\s*1\b/i.test(slice) ||
        /"memberDistance"\s*:\s*\{\s*"value"\s*:\s*"DISTANCE_1"/i.test(slice)
      ) {
        return true
      }
      from = idx + Math.max(v.length, 1)
    }
  }
  return false
}

function hasPrimaryMessageCta() {
  for (const el of profileActionButtons()) {
    const label = buttonLabel(el)
    if (
      /\b(message|mensaje|enviar mensaje|messaging)\b/.test(label) &&
      !/\b(connect|conectar|contactar)\b/.test(label)
    ) {
      return true
    }
  }
  return false
}

function slugIdentityVariants(slug) {
  const raw = String(slug || '').trim()
  const out = new Set()
  const add = (s) => {
    const v = String(s || '').trim()
    if (v) out.add(v)
  }
  add(raw)
  try {
    add(decodeURIComponent(raw))
  } catch {
    /* ignore */
  }
  try {
    const decoded = decodeURIComponent(raw)
    // NO agregar encodeURIComponent(decoded): memberIdentity ya se encodea en la URL
    // y "mia-%C3%A1lvarez" terminaba double-encoded (rompe perfiles con acento).
    if (typeof decoded.normalize === 'function') {
      add(decoded.normalize('NFC'))
      add(decoded.normalize('NFD'))
      add(decoded.normalize('NFD').replace(/\p{M}/gu, ''))
    }
    add(decoded.toLowerCase())
  } catch {
    /* ignore */
  }
  return [...out]
}

function degreeFromText(raw) {
  const t = String(raw || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  if (!t || t.length > 48) return null

  // Badge corto: "1er" | "· 1er" | "2º" | "2do" | "3ro"
  if (/^(?:·|•)?\s*1(?:er|º|°|st|ro)?$/.test(t)) return 1
  if (/^(?:·|•)?\s*2(?:º|°|nd|do)?$/.test(t)) return 2
  if (/^(?:·|•)?\s*3(?:er|º|°|rd|ro)?$/.test(t)) return 3

  // Sin \b tras º/° (rompe "· 2º" / "Nombre · 2º").
  if (
    /[·•]\s*1(?:er|º|°|st|ro)?/i.test(t) ||
    /\b1(?:er|st|ro)\b/i.test(t) ||
    /\b1[º°]/i.test(t) ||
    /\b1(?:er)?\s*grado\b/i.test(t) ||
    /\b1st\s*degree\b/i.test(t)
  ) {
    return 1
  }
  if (
    /[·•]\s*2(?:º|°|nd|do)?/i.test(t) ||
    /\b2(?:nd|do)\b/i.test(t) ||
    /\b2[º°]/i.test(t) ||
    /\b2(?:do)?\s*grado\b/i.test(t) ||
    /\b2nd\s*degree\b/i.test(t)
  ) {
    return 2
  }
  if (
    /[·•]\s*3(?:er|º|°|rd|ro)?/i.test(t) ||
    /\b3(?:er|rd|ro)\b/i.test(t) ||
    /\b3[º°]/i.test(t) ||
    /\b3(?:er|ro)?\s*grado\b/i.test(t) ||
    /\b3rd\s*degree\b/i.test(t)
  ) {
    return 3
  }
  return null
}

function hasCustomInviteLink() {
  return Boolean(
    document.querySelector(
      'a[href*="/preload/custom-invite"], a[href*="custom-invite/?vanityName"], a[href*="custom-invite?"]',
    ),
  )
}

function profileActionRoots() {
  return [
    ...document.querySelectorAll(
      [
        '.pvs-profile-actions',
        '.pv-top-card-v2-ctas',
        '.ph5.pb5',
        'section.artdeco-card .ph5',
        'main .pv-top-card',
        '[data-view-name="profile-top-card"]',
      ].join(', '),
    ),
  ]
}

function profileActionButtons() {
  const roots = profileActionRoots()
  if (!roots.length) return []
  const buttons = []
  for (const root of roots) {
    buttons.push(
      ...root.querySelectorAll('button, a[role="button"], div[role="button"], a.artdeco-button'),
    )
  }
  return buttons
}

function buttonLabel(el) {
  return `${el.getAttribute('aria-label') || ''} ${el.textContent || ''}`
    .toLowerCase()
    .replace(/\s+/g, ' ')
}

function hasVisibleConnectInTopCard() {
  // SOLO texto visible Contactar/Conectar — NO data-control-name (da falsos en perfiles 1er).
  const buttons = profileActionButtons()
  // Si no hay roots, escanear botones del main (LinkedIn cambia layout).
  const pool =
    buttons.length > 0
      ? buttons
      : [
          ...document.querySelectorAll(
            'main button, main a[role="button"], main a.artdeco-button, main div[role="button"]',
          ),
        ]
  for (const el of pool) {
    const label = buttonLabel(el).trim()
    if (!label || label.length > 100) continue
    if (/\b(pending|pendiente|connected|conectado|mensaje|message|seguir|follow)\b/.test(label)) {
      continue
    }
    if (/^(connect|conectar|contactar)\b/.test(label)) return true
    if (/\binvitar a conectar\b/.test(label)) return true
    if (/\binvite\b/.test(label) && /\bconnect\b/.test(label)) return true
    if (/^invite\b/.test(label)) return true
  }
  return false
}

function detectFirstDegreeOnProfile() {
  return readProfileDegreeBadge() === 1
}

function detectNotConnectedOnProfile() {
  const degree = readProfileDegreeBadge()
  if (degree === 1) return false
  if (degree === 2 || degree === 3) return true
  // No usar solo Contactar: en perfiles 1er a veces hay menús con "conectar" residual.
  return false
}

/**
 * @returns {Promise<'connected'|'not_connected'|null>}
 */
async function fetchDistanceViaVoyager(slug) {
  const token = await csrfTokenAsync()
  if (!token || !slug) return null

  const uniq = slugIdentityVariants(slug)
  const decorations = [
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-128',
    'com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-16',
    'com.linkedin.voyager.dash.deco.identity.profile.TopCardCore-16',
  ]

  const headers = {
    accept: 'application/vnd.linkedin.normalized+json+2.1',
    'csrf-token': token,
    'x-restli-protocol-version': '2.0.0',
    'x-li-lang': document.documentElement?.lang || 'es_ES',
  }

  for (const identity of uniq) {
    try {
      const url = `https://www.linkedin.com/voyager/api/identity/profiles/${encodeURIComponent(identity)}/networkinfo`
      const res = await fetch(url, { method: 'GET', credentials: 'include', headers })
      if (!res.ok) continue
      const data = await res.json()
      const dist = data?.data?.distance || data?.distance
      const verdict = distanceTokenToVerdict(dist?.value ?? dist)
      if (verdict) return verdict
    } catch {
      /* next */
    }
  }

  for (const identity of uniq) {
    for (const decorationId of decorations) {
      try {
        const url =
          `https://www.linkedin.com/voyager/api/identity/dash/profiles` +
          `?q=memberIdentity&memberIdentity=${encodeURIComponent(identity)}` +
          `&decorationId=${encodeURIComponent(decorationId)}`
        const res = await fetch(url, {
          method: 'GET',
          credentials: 'include',
          headers,
        })
        if (!res.ok) continue
        const data = await res.json()
        const verdict = parseMemberDistanceFromJson(data, identity)
        if (verdict) return verdict
        const primary = parseDistanceFromPrimaryEntity(data)
        if (primary) return primary
      } catch {
        /* next */
      }
    }
  }

  for (const identity of uniq) {
    try {
      const url = `https://www.linkedin.com/voyager/api/identity/profileView/${encodeURIComponent(identity)}`
      const res = await fetch(url, { method: 'GET', credentials: 'include', headers })
      if (!res.ok) continue
      const data = await res.json()
      const verdict = parseMemberDistanceFromJson(data, identity)
      if (verdict) return verdict
      const primary = parseDistanceFromPrimaryEntity(data)
      if (primary) return primary
    } catch {
      /* next */
    }
  }
  return null
}

function parseDistanceFromPrimaryEntity(data) {
  if (!data || typeof data !== 'object') return null
  const primary = data.data
  if (!primary || typeof primary !== 'object' || Array.isArray(primary)) return null
  const raw =
    primary.memberDistance?.value ||
    primary.memberDistance ||
    primary.networkDistance ||
    primary.distance
  return distanceTokenToVerdict(raw)
}

function parseMemberDistanceFromJson(data, identity) {
  const variants = slugIdentityVariants(identity).map((s) => s.toLowerCase())
  const wants = new Set(variants)
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
  const wantsStripped = new Set([...wants].map(strip))

  const entities = []
  if (data && typeof data === 'object') {
    if (Array.isArray(data.included)) entities.push(...data.included)
    if (data.data) {
      if (Array.isArray(data.data)) entities.push(...data.data)
      else entities.push(data.data)
    }
  }

  for (const ent of entities) {
    if (!ent || typeof ent !== 'object') continue
    const pub = String(ent.publicIdentifier || '').toLowerCase()
    if (!pub) continue
    const matched =
      wants.has(pub) ||
      wantsStripped.has(strip(pub)) ||
      [...wants].some((w) => {
        try {
          return pub.normalize?.('NFC') === w.normalize?.('NFC')
        } catch {
          return false
        }
      })
    if (!matched) continue
    const raw = ent.memberDistance?.value || ent.memberDistance || ent.networkDistance || ent.distance
    const verdict = distanceTokenToVerdict(raw)
    if (verdict) return verdict
  }

  // Sin scrape de blob JSON: un DISTANCE_1 ajeno cerca del slug genera falsos "aceptó".
  return null
}

function distanceTokenToVerdict(raw) {
  const n = distanceTokenToNumber(raw)
  if (n === 1) return 'connected'
  if (n === 2 || n === 3) return 'not_connected'
  return null
}

function distanceTokenToNumber(raw) {
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

function distanceFromText(text) {
  const t = String(text || '')
  if (/DISTANCE_1\b/i.test(t) || /"networkDistance"\s*:\s*1\b/i.test(t)) return 'connected'
  if (
    /DISTANCE_2\b/i.test(t) ||
    /DISTANCE_3\b/i.test(t) ||
    /OUT_OF_NETWORK\b/i.test(t) ||
    /"networkDistance"\s*:\s*[23]\b/i.test(t)
  ) {
    return 'not_connected'
  }
  return null
}

function slugFromHref(href) {
  try {
    const url = new URL(href, window.location.origin)
    const path = url.pathname.toLowerCase()
    const idx = path.indexOf('/in/')
    if (idx < 0) return null
    const slug = path.slice(idx + 4).split('/')[0]
    return slug ? decodeURIComponent(slug) : null
  } catch {
    return null
  }
}

function scanAcceptanceNotifications() {
  const found = new Set()
  const nodes = document.querySelectorAll(
    '.nt-card, .artdeco-card, .invitation-card, li.mn-connection-card, a[href*="/in/"]',
  )
  for (const node of nodes) {
    const text = (node.textContent || '').toLowerCase()
    if (
      text.includes('aceptó tu invitación') ||
      text.includes('acepto tu invitacion') ||
      text.includes('accepted your invitation') ||
      text.includes('is now a connection') ||
      text.includes('ahora es una conexión')
    ) {
      const link = node.matches('a[href*="/in/"]')
        ? node
        : node.querySelector('a[href*="/in/"]')
      const slug = link ? slugFromHref(link.getAttribute('href')) : null
      if (slug) found.add(slug)
    }
  }
  return [...found]
}

async function resolveProbeProspectId(slug) {
  const probeMatch = String(window.location.search || '').match(/[?&]nexus_probe=(\d+)/i)
  if (probeMatch) return Number(probeMatch[1])
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(
        { type: 'NEXUS_LOOKUP_PROBE_PROSPECT', profileSlug: slug || currentProfileSlug() },
        (res) => {
          if (chrome.runtime.lastError) {
            resolve(null)
            return
          }
          resolve(res?.prospectId ? Number(res.prospectId) : null)
        },
      )
    } catch {
      resolve(null)
    }
  })
}

async function pollConnections(_source) {
  const path = window.location.pathname.toLowerCase()
  const slugs = []
  const notConnectedSlugs = []
  let probeProspectId = null
  let isProbeTab = /[?&]nexus_probe=\d+/i.test(String(window.location.search || ''))

  if (path.includes('/in/')) {
    const slug = currentProfileSlug()
    if (slug) {
      void cacheComposeLinkIfPresent(slug)
      probeProspectId = await resolveProbeProspectId(slug)
      if (probeProspectId) isProbeTab = true
      // Badge 1er/2º/3er (+ Voyager). En pestaña probe SÍ reportamos.
      const verdict = await resolveConnectionVerdict(slug, { fast: isProbeTab })
      if (verdict === 'connected') slugs.push(slug)
      else if (verdict === 'not_connected') notConnectedSlugs.push(slug)
      if (isProbeTab) {
        const degree =
          verdict === 'connected' ? 1 : verdict === 'not_connected' ? 2 : readProfileDegreeBadge()
        // Solo mostrar cartel si YA leyó (el background pinta el diagnóstico completo).
        if (degree === 1 || degree === 2 || degree === 3) {
          showDegreeDebugOverlay(degree)
        }
      }
    }
  }

  if (!isProbeTab && (path.includes('/notifications') || path.includes('/mynetwork'))) {
    slugs.push(...scanAcceptanceNotifications())
  }

  if (!slugs.length && !notConnectedSlugs.length) return

  const stored = await chrome.storage.local.get(CONNECT_SEEN_KEY)
  const seen = stored?.[CONNECT_SEEN_KEY] || {}

  for (const slug of slugs) {
    const key = `conn:${slug.toLowerCase()}`
    if (!isProbeTab && seen[key] && Date.now() - Number(seen[key]) < CONNECT_SEEN_TTL_MS) continue
    await reportConnection(slug, seen, key, {
      status: 'connected',
      prospectId: probeProspectId,
      force: isProbeTab,
    })
  }
  for (const slug of notConnectedSlugs) {
    const key = `conn:${slug.toLowerCase()}:nc`
    if (!isProbeTab && seen[key] && Date.now() - Number(seen[key]) < CONNECT_SEEN_TTL_MS) continue
    await reportConnection(slug, seen, key, {
      status: 'not_connected',
      prospectId: probeProspectId,
      force: isProbeTab,
    })
  }
}

function reportConnection(slug, seen, key, { force = false, prospectId = null, status = 'connected' } = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      {
        type: 'NEXUS_LINKEDIN_CONNECTION_STATUS',
        profileSlug: slug,
        status: String(status || 'connected'),
        prospectId: prospectId || undefined,
        detectedAt: Date.now(),
        force: Boolean(force),
      },
      (response) => {
        if (chrome.runtime.lastError) {
          resolve()
          return
        }
        if (response?.ok) {
          seen[key] = Date.now()
          pruneConnectSeen(seen)
          chrome.storage.local.set({ [CONNECT_SEEN_KEY]: seen })
          if (response?.messageReady && status === 'connected') {
            showConnectToast('Nexus: conexión detectada — mensaje listo para enviar')
          }
        }
        resolve(response)
      },
    )
  })
}

async function forceConnectionCheck(message) {
  // Usar SOLO el slug pedido (no caer al de la página en pings vacíos).
  const slug = String(message?.profileSlug || '').trim().toLowerCase()
  if (!slug) return false
  const prospectId = message?.prospectId || null
  const inviteMode = String(message?.mode || '') === 'invite_sent'

  // Thorough rápido: Voyager+badge (fast). Máx ~4s, no 7s×10.
  for (let i = 0; i < 5; i++) {
    const verdict = inviteMode
      ? await resolveAcceptanceVerdict(slug)
      : await resolveConnectionVerdict(slug, { fast: true })
    if (
      verdict === 'connected' ||
      verdict === 'not_connected' ||
      (inviteMode && verdict === 'not_yet')
    ) {
      if (inviteMode && verdict !== 'connected') {
        return { ok: true, verdict: 'not_yet' }
      }
      const stored = await chrome.storage.local.get(CONNECT_SEEN_KEY)
      const seen = stored?.[CONNECT_SEEN_KEY] || {}
      const key = verdict === 'connected' ? `conn:${slug}` : `conn:${slug}:nc`
      delete seen[key]
      if (verdict === 'connected') delete seen[`conn:${slug}:nc`]
      else delete seen[`conn:${slug}`]
      const res = await reportConnection(slug, seen, key, {
        force: true,
        prospectId,
        status: verdict,
      })
      if (res?.ok) {
        const degree = verdict === 'connected' ? 1 : 2
        showDegreeDebugOverlay(degree)
        return {
          ok: true,
          verdict,
          degree,
          connectionStatus: res?.connectionStatus || verdict,
        }
      }
      return { ok: false, error: 'report_failed', verdict }
    }
    await new Promise((r) => setTimeout(r, 400))
  }
  return { ok: false, error: 'no_verdict' }
}

function pruneConnectSeen(seen) {
  const keys = Object.keys(seen)
  if (keys.length <= MAX_CONNECT_SEEN) return
  keys
    .sort((a, b) => Number(seen[a] || 0) - Number(seen[b] || 0))
    .slice(0, keys.length - MAX_CONNECT_SEEN)
    .forEach((k) => delete seen[k])
}

async function cacheComposeLinkIfPresent(slug) {
  const links = document.querySelectorAll('a[href*="/messaging/compose"]')
  for (const link of links) {
    const href = link.getAttribute('href')
    if (!href) continue
    try {
      const url = new URL(href, window.location.origin)
      if (!url.pathname.includes('/messaging/compose')) continue
      if (!url.searchParams.has('interop')) url.searchParams.set('interop', 'msgOverlay')
      const stored = await chrome.storage.local.get('nexusLiComposeCache')
      const cache = stored?.nexusLiComposeCache || {}
      cache[String(slug).toLowerCase()] = { composeUrl: url.toString(), at: Date.now() }
      await chrome.storage.local.set({ nexusLiComposeCache: cache })
      return
    } catch {
      /* ignore */
    }
  }
}

function showConnectToast(text) {
  try {
    const existing = document.getElementById('nexus-li-connect-toast')
    if (existing) existing.remove()
    const el = document.createElement('div')
    el.id = 'nexus-li-connect-toast'
    el.textContent = text
    el.style.cssText =
      'position:fixed;z-index:2147483647;bottom:24px;right:24px;max-width:320px;' +
      'background:#0A66C2;color:#fff;padding:12px 16px;border-radius:10px;' +
      'font:600 13px/1.35 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25)'
    document.documentElement.appendChild(el)
    setTimeout(() => el.remove(), 4500)
  } catch {
    // ignore
  }
}
