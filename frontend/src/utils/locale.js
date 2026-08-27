const STORAGE_KEY = 'nexus_ui_locale'

/** @typedef {'es' | 'en'} UiLocale */

/** @type {Record<UiLocale, { label: string, nativeLabel: string, htmlLang: string, dateLocale: string }>} */
export const UI_LOCALES = {
  es: {
    label: 'Español',
    nativeLabel: 'Español (Latinoamérica)',
    htmlLang: 'es',
    dateLocale: 'es-AR',
  },
  en: {
    label: 'English',
    nativeLabel: 'English',
    htmlLang: 'en',
    dateLocale: 'en-US',
  },
}

/** @returns {UiLocale} */
export function getStoredUiLocale() {
  try {
    const raw = String(localStorage.getItem(STORAGE_KEY) || '').trim().toLowerCase()
    if (raw === 'en' || raw === 'es') return raw
  } catch {
    /* ignore */
  }
  return 'es'
}

/** @param {UiLocale} locale */
export function setStoredUiLocale(locale) {
  const next = locale === 'en' ? 'en' : 'es'
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* ignore */
  }
  applyDocumentLocale(next)
  return next
}

/** @param {UiLocale} [locale] */
export function applyDocumentLocale(locale) {
  const loc = locale || getStoredUiLocale()
  const meta = UI_LOCALES[loc] || UI_LOCALES.es
  if (typeof document !== 'undefined') {
    document.documentElement.lang = meta.htmlLang
  }
  return loc
}
