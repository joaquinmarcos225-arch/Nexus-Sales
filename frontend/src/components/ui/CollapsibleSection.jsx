import { useState } from 'react'

export function CollapsibleSection({
  title,
  subtitle,
  badge,
  defaultOpen = false,
  accent = 'brand',
  children,
}) {
  const [open, setOpen] = useState(defaultOpen)

  const accentRing =
    accent === 'notifications'
      ? 'ring-amber-200/60 hover:ring-amber-300/70'
      : 'ring-rose-200/50 hover:ring-rose-300/60'

  return (
    <section
      className={`overflow-hidden rounded-xl border border-zinc-200/90 bg-white shadow-sm shadow-zinc-900/5 ring-1 transition-shadow ${accentRing}`}
    >
      <button
        type="button"
        className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-zinc-50/80 sm:px-5"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br ${
            accent === 'notifications'
              ? 'from-amber-600 to-zinc-900'
              : 'from-red-700 via-rose-900 to-zinc-900'
          } text-white shadow-sm`}
        >
          <Chevron open={open} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-zinc-900">{title}</span>
          {subtitle ? (
            <span className="mt-0.5 block text-xs text-zinc-500">{subtitle}</span>
          ) : null}
        </span>
        {badge != null ? (
          <span className="shrink-0 rounded-full bg-zinc-900 px-2.5 py-0.5 text-[11px] font-bold text-white">
            {badge}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className="border-t border-zinc-100 px-4 py-4 sm:px-5 sm:py-5">{children}</div>
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
