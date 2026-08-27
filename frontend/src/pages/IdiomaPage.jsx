import { useEffect, useState } from 'react'
import { applyDocumentLocale, getStoredUiLocale, setStoredUiLocale, UI_LOCALES } from '../utils/locale.js'

/**
 * Preferencia de idioma de la interfaz (se guarda en este navegador).
 */
export default function IdiomaPage() {
  const [locale, setLocale] = useState(() => getStoredUiLocale())
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    applyDocumentLocale(locale)
  }, [locale])

  function choose(next) {
    const applied = setStoredUiLocale(next)
    setLocale(applied)
    setSaved(true)
    window.setTimeout(() => setSaved(false), 2200)
  }

  return (
    <div className="mx-auto max-w-xl space-y-5">
      <div>
        <h2 className="text-base font-semibold text-nx-ink">Idioma de la interfaz</h2>
        <p className="mt-1 text-sm text-nx-muted">
          Elegí el idioma en el que querés ver Nexus en este dispositivo. La preferencia se guarda
          solo en tu navegador.
        </p>
      </div>

      <div className="space-y-2">
        {(Object.keys(UI_LOCALES)).map((code) => {
          const meta = UI_LOCALES[code]
          const active = locale === code
          return (
            <button
              key={code}
              type="button"
              onClick={() => choose(code)}
              className={[
                'flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition',
                active
                  ? 'border-nx-brand bg-nx-brand/5 ring-1 ring-nx-brand/30'
                  : 'border-nx-border bg-white hover:border-nx-border-strong hover:bg-nx-card-muted/40',
              ].join(' ')}
            >
              <span>
                <span className="block text-sm font-semibold text-nx-ink">{meta.nativeLabel}</span>
                <span className="mt-0.5 block text-xs text-nx-muted">{meta.label}</span>
              </span>
              {active ? (
                <span className="rounded-full bg-nx-brand px-2.5 py-0.5 text-[11px] font-semibold text-white">
                  Activo
                </span>
              ) : (
                <span className="text-xs font-medium text-nx-muted">Usar</span>
              )}
            </button>
          )
        })}
      </div>

      {saved ? (
        <p className="text-sm font-medium text-nx-brand" role="status">
          Idioma actualizado.
        </p>
      ) : null}

      <p className="rounded-lg border border-nx-border bg-nx-card-muted/40 px-3 py-2 text-xs leading-relaxed text-nx-muted">
        Hoy la app está optimizada en español. Si elegís English, se guarda la preferencia y el
        formato de fechas/idioma del documento; la traducción completa de pantallas se irá
        ampliando.
      </p>
    </div>
  )
}
