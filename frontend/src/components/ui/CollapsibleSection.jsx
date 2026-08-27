import { useEffect, useState } from 'react'

export function CollapsibleSection({
  id,
  title,
  subtitle,
  badge,
  defaultOpen = false,
  open: openProp,
  onOpenChange,
  tone = 'default',
  children,
}) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const open = openProp !== undefined ? openProp : internalOpen

  useEffect(() => {
    if (defaultOpen) {
      setInternalOpen(true)
    }
  }, [defaultOpen])

  function setOpen(next) {
    const value = typeof next === 'function' ? next(open) : next
    onOpenChange?.(value)
    if (openProp === undefined) {
      setInternalOpen(value)
    }
  }

  const toneAttr =
    tone === 'linkedin' || tone === 'whatsapp' || tone === 'mail' || tone === 'call' ? tone : undefined

  return (
    <section
      id={id}
      className="nx-fold-panel transition-shadow"
      data-tone={toneAttr}
    >
      <button
        type="button"
        className="nx-fold-header flex w-full items-center gap-2.5 px-4 py-2.5 text-left transition sm:px-5"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="nx-fold-chevron flex h-6 w-6 shrink-0 items-center justify-center rounded-md border shadow-none">
          <Chevron open={open} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="nx-fold-title block text-sm font-semibold leading-snug">{title}</span>
          {subtitle ? (
            <span className="nx-fold-subtitle mt-0.5 block text-xs leading-snug">{subtitle}</span>
          ) : null}
        </span>
        {badge != null ? (
          <span className="nx-fold-badge shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold">
            {badge}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className="nx-fold-body px-4 py-3 sm:px-5">{children}</div>
      ) : null}
    </section>
  )
}

function Chevron({ open }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`size-4 transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      aria-hidden
    >
      <path d="M9 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
