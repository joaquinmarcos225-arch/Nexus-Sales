import { useState } from 'react'

/**
 * Bloque de página con título; opcionalmente colapsable.
 * Cabecera fina (nx-fold-header); cuerpo claro.
 */
export function PageSection({
  title,
  description,
  defaultOpen = true,
  collapsible = true,
  children,
  actions,
  className = '',
}) {
  const [open, setOpen] = useState(defaultOpen)

  const header = (
    <div className="flex min-w-0 flex-1 items-center justify-between gap-3">
      <div className="min-w-0">
        <h2 className="nx-fold-title text-sm font-semibold leading-snug">{title}</h2>
        {description ? (
          <p className="nx-fold-subtitle mt-0.5 text-xs leading-snug">{description}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {actions}
        {collapsible ? (
          <svg
            viewBox="0 0 24 24"
            className={[
              'size-4 text-nx-brand/70 transition-transform',
              open ? 'rotate-180' : '',
            ].join(' ')}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
          </svg>
        ) : null}
      </div>
    </div>
  )

  return (
    <section className={['nx-fold-panel', className].filter(Boolean).join(' ')}>
      {collapsible ? (
        <button
          type="button"
          className="nx-fold-header flex w-full px-4 py-2.5 text-left transition sm:px-5"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {header}
        </button>
      ) : (
        <div className="nx-fold-header px-4 py-2.5 sm:px-5">{header}</div>
      )}
      {open || !collapsible ? (
        <div
          className={
            collapsible
              ? 'nx-fold-body px-4 py-3 sm:px-5'
              : 'nx-fold-body px-4 pb-3 sm:px-5'
          }
        >
          {children}
        </div>
      ) : null}
    </section>
  )
}
