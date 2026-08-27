/**
 * Resuelve slug público → URL de compose de LinkedIn.
 * Corre inyectado en una pestaña linkedin.com (usa cookies de sesión).
 *
 * Formato real del botón Mensaje:
 * https://www.linkedin.com/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3A{ID}
 *   &recipient={ID}&screenContext=NON_SELF_PROFILE_VIEW&interop=msgOverlay
 */
window.__NEXUS_LI_COMPOSE__ = {
  resolveComposeUrl,
  buildComposeUrl,
  extractComposeFromDom,
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

function extractComposeFromDom() {
  for (const el of document.querySelectorAll('a[href*="/messaging/compose"]')) {
    const href = el.getAttribute('href')
    if (!href) continue
    try {
      const url = new URL(href, location.origin)
      if (!url.pathname.includes('/messaging/compose')) continue
      if (!url.searchParams.has('interop')) url.searchParams.set('interop', 'msgOverlay')
      if (!url.searchParams.has('screenContext')) {
        url.searchParams.set('screenContext', 'NON_SELF_PROFILE_VIEW')
      }
      return url.toString()
    } catch {
      /* ignore */
    }
  }
  return null
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

function parseUrnId(raw) {
  const value = String(raw || '')
  const m = value.match(/urn:li:fsd_profile:([A-Za-z0-9_-]+)/)
  if (m) return m[1]
  if (/^[A-Za-z0-9_-]{8,}$/.test(value)) return value
  return null
}

function pickUrnFromVoyagerJson(data, slug) {
  const want = String(slug || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
  const included = Array.isArray(data?.included) ? data.included : []
  const entities = []
  if (data?.data) entities.push(data.data)
  entities.push(...included)

  for (const ent of entities) {
    if (!ent || typeof ent !== 'object') continue
    const pub = String(ent.publicIdentifier || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
    const urn =
      parseUrnId(ent.entityUrn) ||
      parseUrnId(ent['*entityUrn']) ||
      parseUrnId(ent.profileUrn) ||
      parseUrnId(ent['*profileUrn'])
    if (urn && pub && want && (pub === want || pub.includes(want) || want.includes(pub))) {
      return urn
    }
  }

  for (const ent of entities) {
    const actions = ent?.profileStatefulProfileActions
    const overflow = actions?.overflowActions || []
    for (const item of overflow) {
      const id = item?.report?.authorProfileId || item?.shareViaMessage?.authorProfileId
      if (id) return String(id)
    }
    const primary = actions?.primaryAction
    if (primary?.shareViaMessage?.authorProfileId) {
      return String(primary.shareViaMessage.authorProfileId)
    }
  }

  for (const ent of entities) {
    const urn = parseUrnId(ent?.entityUrn) || parseUrnId(ent?.['*entityUrn'])
    if (urn && (ent?.publicIdentifier || ent?.firstName)) return urn
  }
  return null
}

async function resolveUrnViaVoyager(slug) {
  const candidates = []
  const raw = String(slug || '').trim()
  if (!raw) return null
  candidates.push(raw)
  try {
    candidates.push(decodeURIComponent(raw))
  } catch {
    /* ignore */
  }
  try {
    candidates.push(encodeURIComponent(decodeURIComponent(raw)))
  } catch {
    candidates.push(encodeURIComponent(raw))
  }
  const uniq = [...new Set(candidates.filter(Boolean))]

  const token = csrfToken()
  for (const identity of uniq) {
    try {
      const url =
        `https://www.linkedin.com/voyager/api/identity/dash/profiles` +
        `?q=memberIdentity&memberIdentity=${encodeURIComponent(identity)}` +
        `&decorationId=com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-128`
      const res = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        headers: {
          accept: 'application/vnd.linkedin.normalized+json+2.1',
          'csrf-token': token,
          'x-restli-protocol-version': '2.0.0',
          'x-li-lang': 'es_ES',
        },
      })
      if (!res.ok) continue
      const data = await res.json()
      const urn = pickUrnFromVoyagerJson(data, identity)
      if (urn) return urn
    } catch {
      /* try next */
    }
  }
  return null
}

function scrapeUrnFromPageHtml(slug) {
  try {
    const blob = (document.documentElement?.innerHTML || '').slice(0, 900000)
    const want = String(slug || '').toLowerCase()
    if (want) {
      const lower = blob.toLowerCase()
      let from = 0
      while (from < lower.length) {
        const idx = lower.indexOf(want, from)
        if (idx < 0) break
        const slice = blob.slice(Math.max(0, idx - 2500), idx + want.length + 2500)
        const m = slice.match(/urn:li:fsd_profile:([A-Za-z0-9_-]+)/)
        if (m) return m[1]
        from = idx + Math.max(want.length, 1)
      }
      const tail = want.split('-').pop()
      if (tail && tail.length >= 6) {
        const near = blob.match(new RegExp(`urn:li:fsd_profile:([A-Za-z0-9_-]*${tail}[A-Za-z0-9_-]*)`, 'i'))
        if (near) return near[1]
      }
    }
    const href = blob.match(/profileUrn=urn(?:%3A|:)li(?:%3A|:)fsd_profile(?:%3A|:)([A-Za-z0-9_-]+)/i)
    if (href) return href[1]
  } catch {
    /* ignore */
  }
  return null
}

/**
 * @param {string} slug publicIdentifier del perfil
 * @returns {Promise<{ composeUrl: string|null, method: string }>}
 */
async function resolveComposeUrl(slug) {
  const fromDom = extractComposeFromDom()
  if (fromDom) return { composeUrl: fromDom, method: 'dom-link' }

  const voyagerUrn = await resolveUrnViaVoyager(slug)
  if (voyagerUrn) {
    return { composeUrl: buildComposeUrl(voyagerUrn), method: 'voyager', urn: voyagerUrn }
  }

  const htmlUrn = scrapeUrnFromPageHtml(slug)
  if (htmlUrn) {
    return { composeUrl: buildComposeUrl(htmlUrn), method: 'html-scrape', urn: htmlUrn }
  }

  return { composeUrl: null, method: 'failed' }
}
