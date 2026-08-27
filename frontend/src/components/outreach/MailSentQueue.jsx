import { useEffect, useState } from 'react'
import { QueueDayHeader } from './QueueDayHeader.jsx'

function itemKey(item) {
  return Number(item?.outreach_message_id || item?.prospect_id)
}

function formatSentAt(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('es-AR', {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return String(iso)
  }
}

/**
 * Cola Mail — notificación de mails enviados (sin acciones de envío).
 * Tocar la card despliega asunto + cuerpo.
 */
export function MailSentQueue({
  items = [],
  days = [],
  pendingTotal = 0,
  limit = null,
  remainingToday = null,
}) {
  const [expandedId, setExpandedId] = useState(null)

  useEffect(() => {
    if (expandedId == null) return
    const stillThere = (items || []).some((t) => itemKey(t) === Number(expandedId))
    if (!stillThere) setExpandedId(null)
  }, [items, expandedId])

  if (!items.length && !pendingTotal) {
    return (
      <p className="rounded-lg border border-dashed border-orange-200 bg-orange-50/40 px-3 py-4 text-center text-[12px] text-nx-muted">
        Todavía no se envió ningún mail en esta campaña.
      </p>
    )
  }

  function openOnly(id) {
    const n = Number(id)
    setExpandedId((cur) => (Number(cur) === n ? null : n))
  }

  function ItemCard({ item }) {
    const id = itemKey(item)
    const expanded = Number(expandedId) === id
    const sentLabel = formatSentAt(item.sent_at)

    return (
      <article
        className={[
          'flex flex-col rounded-xl border p-2.5 shadow-sm transition-shadow',
          'min-h-[5.5rem]',
          'border-orange-200/80 bg-white ring-1 ring-orange-100',
          expanded ? 'shadow-md ring-2 ring-orange-300/50' : 'hover:shadow-md',
        ].join(' ')}
      >
        <button
          type="button"
          className="flex w-full items-start justify-between gap-1.5 text-left"
          aria-expanded={expanded}
          onClick={() => openOnly(id)}
        >
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold leading-tight text-nx-ink">
              {item.prospect_name}
            </p>
            {item.company_name ? (
              <p className="mt-0.5 truncate text-[10px] text-nx-muted">{item.company_name}</p>
            ) : null}
            {!expanded && item.subject ? (
              <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-nx-ink/80">
                {item.subject}
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <span className="rounded-full bg-orange-50 px-2 py-0.5 text-[10px] font-semibold text-orange-900 ring-1 ring-orange-200">
              Enviado
            </span>
            {sentLabel ? (
              <span className="text-[10px] tabular-nums text-nx-muted">{sentLabel}</span>
            ) : null}
            <svg
              viewBox="0 0 24 24"
              className={`size-3.5 shrink-0 text-nx-muted transition-transform ${expanded ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              aria-hidden
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </div>
        </button>

        {expanded ? (
          <div className="mt-2 space-y-2 border-t border-orange-100 pt-2">
            {item.email ? (
              <p className="text-[10px] text-nx-muted">{item.email}</p>
            ) : null}
            <p className="text-[12px] font-semibold text-nx-ink">{item.subject || '(Sin asunto)'}</p>
            <p className="max-h-48 overflow-y-auto rounded-lg bg-orange-50/50 p-2 text-[11px] leading-relaxed text-nx-ink whitespace-pre-wrap ring-1 ring-orange-100">
              {item.body || 'Sin cuerpo.'}
            </p>
            {item.gmail_web_link ? (
              <a
                href={item.gmail_web_link}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex text-[11px] font-semibold text-orange-800 underline-offset-2 hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                Abrir en Gmail
              </a>
            ) : null}
          </div>
        ) : null}
      </article>
    )
  }

  function PendingCard({ item }) {
    return (
      <article className="flex flex-col rounded-xl border border-orange-200/70 bg-orange-50/30 p-2.5 ring-1 ring-orange-100">
        <p className="truncate text-sm font-semibold text-nx-ink">{item.prospect_name}</p>
        {item.company_name ? (
          <p className="truncate text-[10px] text-nx-muted">{item.company_name}</p>
        ) : null}
        <p className="mt-1 line-clamp-2 text-[11px] text-nx-ink/80">{item.subject || '(Sin asunto)'}</p>
        <span className="mt-2 w-fit rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold text-orange-900 ring-1 ring-orange-200">
          Pendiente de envío
        </span>
      </article>
    )
  }

  return (
    <div className="space-y-4">
      {limit != null ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-nx-muted">
          <span className="rounded-full bg-orange-50 px-2.5 py-1 font-medium text-orange-950 ring-1 ring-orange-200">
            Email hoy:{' '}
            <span className="font-semibold text-nx-ink">
              {Math.max(0, limit - (remainingToday ?? limit))}/{limit}
            </span>
          </span>
          {pendingTotal > (days[0]?.scheduled ?? 0) ? (
            <span className="rounded-full bg-zinc-50 px-2.5 py-1 font-semibold text-zinc-900 ring-1 ring-zinc-200">
              {pendingTotal - (days[0]?.scheduled ?? 0)} email
              {pendingTotal - (days[0]?.scheduled ?? 0) === 1 ? '' : 's'} en días siguientes
            </span>
          ) : null}
        </div>
      ) : null}

      {Array.isArray(days) && days.length > 0 ? (
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-nx-muted">Programación pendiente</h3>
          {days.map((day) => {
            const pendingItems = day.items || []
            if (!pendingItems.length) return null
            const readOnly = day.actionable === false || Number(day.day_offset) > 0
            return (
              <div key={`mail-day-${day.day_offset}`} className="space-y-2">
                <QueueDayHeader
                  label={day.label || (day.day_offset === 0 ? 'Hoy' : `Día +${day.day_offset}`)}
                  scheduled={day.scheduled ?? pendingItems.length}
                  limit={day.limit ?? limit}
                  actionable={!readOnly}
                />
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                  {pendingItems.map((item) => (
                    <PendingCard key={`pending-${day.day_offset}-${item.prospect_id}`} item={item} />
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      ) : null}

      {items.length ? (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-nx-muted">Enviados</h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {items.map((item) => (
              <ItemCard key={`mail-${itemKey(item)}`} item={item} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
