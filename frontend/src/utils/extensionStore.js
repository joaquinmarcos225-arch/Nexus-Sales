/** Item publicado (unlisted) — URL canónica que abre bien en Chrome. */
export const NEXUS_EXTENSION_STORE_URL =
  'https://chromewebstore.google.com/detail/nexus-sales-%E2%80%94-outreach-as/bpckohapoojkbbikcnjpahblaacbnnnb'

export function resolveExtensionStoreUrl() {
  const raw =
    (typeof import.meta !== 'undefined' &&
      import.meta.env?.VITE_NEXUS_LINKEDIN_EXTENSION_URL) ||
    ''
  const url = String(raw).trim()
  if (
    url &&
    /^https?:\/\/(chromewebstore\.google\.com|chrome\.google\.com)\/.+/i.test(url)
  ) {
    return url
  }
  return NEXUS_EXTENSION_STORE_URL
}

/** Abre la Store fuera de la SPA/PWA. El <a> solo a veces no alcanza. */
export function openExtensionStore(url = resolveExtensionStoreUrl()) {
  const href = String(url || '').trim()
  if (!href) return false

  try {
    const win = window.open(href, '_blank', 'noopener,noreferrer')
    if (win) return true
  } catch {
    /* seguir con fallback */
  }

  try {
    const a = document.createElement('a')
    a.href = href
    a.target = '_blank'
    a.rel = 'noopener noreferrer'
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    a.remove()
    return true
  } catch {
    /* seguir con fallback */
  }

  window.location.assign(href)
  return true
}
