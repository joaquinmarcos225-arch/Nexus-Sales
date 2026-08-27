import { useEffect, useMemo, useState } from 'react'
import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import {
  whatsappAssistStatusClass,
  whatsappAssistStatusLabel,
  whatsappPriorityClass,
} from '../../utils/whatsappAssist.js'
import { sequenceGroupLabel } from '../../utils/sequenceUi.js'
import { QueueDayHeader } from './QueueDayHeader.jsx'

function taskKey(task) {
  return Number(task?.prospect_id)
}

/**
 * Cola operativa SDR — WhatsApp Assisted (debajo de LinkedIn).
 * Abrir en WhatsApp Web vía extensión.
 */
export function WhatsAppAssistQueue({
  tasks = [],
  days = [],
  freeze = false,
  busyProspectId = null,
  limit = null,
  effectiveLimitToday = null,
  bonusFromReplies = 0,
  remainingToday = null,
  hiddenByCap = 0,
  onOpenWhatsAppWeb,
  onMarkSent,
}) {
  const [expandedId, setExpandedId] = useState(null)

  const dayBuckets = useMemo(() => {
    if (Array.isArray(days) && days.length > 0) return days
    if (!tasks.length) return []
    return [
      {
        day_offset: 0,
        label: 'Hoy',
        actionable: true,
        limit: limit ?? tasks.length,
        scheduled: tasks.length,
        tasks,
      },
    ]
  }, [days, tasks, limit])

  const allTasks = useMemo(
    () => dayBuckets.flatMap((d) => d.tasks || []),
    [dayBuckets],
  )

  useEffect(() => {
    if (expandedId == null) return
    const stillThere = allTasks.some((t) => taskKey(t) === Number(expandedId))
    if (!stillThere) setExpandedId(null)
  }, [allTasks, expandedId])

  if (!allTasks.length && !hiddenByCap) {
    return (
      <p className="rounded-lg border border-dashed border-[#25D366]/35 bg-[#25D366]/5 px-3 py-4 text-center text-[12px] text-nx-muted">
        Sin mensajes WhatsApp pendientes.
      </p>
    )
  }

  function openOnly(prospectId) {
    const id = Number(prospectId)
    setExpandedId((cur) => (Number(cur) === id ? null : id))
  }

  function TaskCard({ task, readOnly = false }) {
    const id = taskKey(task)
    const status = task.assist_status || 'suggested'
    const busy = Number(busyProspectId) === id
    const expanded = Number(expandedId) === id
    const locked = readOnly || freeze

    return (
      <article
        className={[
          'flex flex-col rounded-xl border p-2.5 shadow-sm transition-shadow',
          'min-h-[7.5rem]',
          'border-[#25D366]/40 bg-white ring-1 ring-[#25D366]/15',
          expanded ? 'shadow-md ring-2 ring-[#25D366]/35' : 'hover:shadow-md',
          readOnly ? 'opacity-75' : '',
        ].join(' ')}
      >
        {readOnly ? (
          <p className="mb-1 text-[10px] font-medium text-zinc-500">Programado · aún no disponible hoy</p>
        ) : null}
        <button
          type="button"
          className="flex w-full items-start justify-between gap-1.5 text-left"
          aria-expanded={expanded}
          onClick={() => openOnly(id)}
        >
          <p className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-nx-ink">
            {task.prospect_name}
          </p>
          <div className="flex shrink-0 items-center gap-1">
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${whatsappAssistStatusClass(status)}`}
            >
              {whatsappAssistStatusLabel(status)}
            </span>
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

        <div className="mt-auto space-y-1.5 pt-2">
          <PremiumGradientButton
            fullWidth
            className="!bg-[#25D366] !py-1.5 !text-xs hover:!bg-[#1da851] focus-visible:!ring-[#25D366]"
            disabled={locked || busy}
            onClick={() => onOpenWhatsAppWeb?.(task)}
          >
            {busy ? 'Abriendo…' : readOnly ? 'WhatsApp Web (programado)' : 'WhatsApp Web'}
          </PremiumGradientButton>
          <button
            type="button"
            disabled={locked || busy}
            className="w-full rounded-lg border border-emerald-200 bg-emerald-50/90 py-1.5 text-[11px] font-semibold text-emerald-950 hover:bg-emerald-100 disabled:opacity-40"
            onClick={() => onMarkSent?.(task)}
          >
            Marcar como enviado
          </button>
        </div>

        {expanded ? (
          <div className="mt-2 space-y-2 border-t border-[#25D366]/25 pt-2">
            <p className="text-[10px] font-medium text-nx-muted">
              {task.phone_display || task.phone_digits || 'Sin teléfono'}
              {task.company_name ? (
                <span className="text-nx-subtle"> · {task.company_name}</span>
              ) : null}
              {task.sequence_group ? (
                <span className="text-nx-subtle"> · {sequenceGroupLabel(task.sequence_group)}</span>
              ) : null}
              <span
                className={`ml-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ring-1 ${whatsappPriorityClass(task.priority)}`}
              >
                {task.priority || 'media'}
              </span>
            </p>
            <p className="max-h-40 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-nx-ink">
              {task.message || 'Sin mensaje.'}
            </p>
          </div>
        ) : null}
      </article>
    )
  }

  const showCaps = limit != null
  const todayCap = effectiveLimitToday ?? limit
  const sentToday = todayCap != null && remainingToday != null ? Math.max(0, todayCap - remainingToday) : null

  return (
    <div className="space-y-4">
      {showCaps ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-nx-muted">
          <span className="rounded-full bg-[#25D366]/10 px-2.5 py-1 font-medium text-emerald-900 ring-1 ring-[#25D366]/25">
            WhatsApp hoy:{' '}
            <span className="font-semibold text-nx-ink">
              {sentToday}/{todayCap}
            </span>
            {bonusFromReplies > 0 ? (
              <span className="text-emerald-800"> (+{bonusFromReplies} por respuestas)</span>
            ) : null}
          </span>
          {hiddenByCap > 0 ? (
            <span className="rounded-full bg-zinc-50 px-2.5 py-1 font-semibold text-zinc-900 ring-1 ring-zinc-200">
              {hiddenByCap} programado{hiddenByCap === 1 ? '' : 's'} en días siguientes
            </span>
          ) : null}
        </div>
      ) : null}

      {dayBuckets.map((day) => {
        const dayTasks = day.tasks || []
        if (!dayTasks.length) return null
        const readOnly = day.actionable === false || Number(day.day_offset) > 0
        return (
          <div key={`wa-day-${day.day_offset}`} className="space-y-2">
            <QueueDayHeader
              label={day.label || (day.day_offset === 0 ? 'Hoy' : `Día +${day.day_offset}`)}
              scheduled={day.scheduled ?? dayTasks.length}
              limit={day.limit ?? limit}
              actionable={!readOnly}
            />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {dayTasks.map((task) => (
                <TaskCard key={`wa-${day.day_offset}-${taskKey(task)}`} task={task} readOnly={readOnly} />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
