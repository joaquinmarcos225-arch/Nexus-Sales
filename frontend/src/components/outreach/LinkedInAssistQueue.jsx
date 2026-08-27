import { useEffect, useMemo, useState } from 'react'
import {
  linkedInAssistStatusClass,
  linkedInAssistStatusLabel,
  linkedInUrlLabel,
} from '../../utils/linkedinAssist.js'
import { wasLiContactarDone } from '../../utils/linkedinLiSafe.js'
import { sequenceGroupLabel } from '../../utils/sequenceUi.js'
import { QueueDayHeader } from './QueueDayHeader.jsx'

function taskKey(task) {
  return Number(task?.prospect_id)
}

function taskMatchesQuery(task, q) {
  if (!q) return true
  const hay = [task.prospect_name, task.company_name, task.linkedin_url, task.message]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  return hay.includes(q)
}

/** Tilde gris (pendiente / clickeable) o verde (hecho). */
function StepCheck({ done, clickable = false, disabled = false, title, onClick }) {
  if (done) {
    return (
      <span
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-[12px] font-bold text-white shadow-sm"
        title={title || 'Hecho'}
        aria-label={title || 'Hecho'}
      >
        ✓
      </span>
    )
  }
  if (clickable) {
    return (
      <button
        type="button"
        disabled={disabled}
        title={title || 'Confirmar'}
        aria-label={title || 'Confirmar'}
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 border-zinc-300 bg-zinc-100 text-[11px] font-semibold text-zinc-400 hover:border-emerald-400 hover:bg-emerald-50 hover:text-emerald-600 disabled:cursor-not-allowed disabled:opacity-40"
        onClick={onClick}
      >
        ✓
      </button>
    )
  }
  return (
    <span
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 border-zinc-300 bg-zinc-100 text-[11px] font-semibold text-zinc-400"
      title={title || 'Pendiente'}
      aria-label={title || 'Pendiente'}
    >
      ✓
    </span>
  )
}

/**
 * LI-SAFE: Contactar + tilde auto; Enviar mensaje + tilde manual (= confirmar enviado).
 * Buscador + scroll para colas grandes (~200).
 */
export function LinkedInAssistQueue({
  tasks = [],
  days = [],
  freeze = false,
  busyProspectId = null,
  onContactar,
  onEnviarMensaje,
  onOpenLinkedIn,
  onMarkSent,
  invitesRemaining = null,
  invitesLimit = null,
  dmsRemaining = null,
  dmsLimit = null,
  hiddenByCap = 0,
  contactarTick = 0,
}) {
  const [expandedId, setExpandedId] = useState(null)
  const [query, setQuery] = useState('')

  const dayBuckets = useMemo(() => {
    if (Array.isArray(days) && days.length > 0) return days
    if (!tasks.length) return []
    return [
      {
        day_offset: 0,
        label: 'Hoy',
        actionable: true,
        invites_limit: invitesLimit ?? 0,
        invites_scheduled: tasks.filter((t) => t.action === 'connect').length,
        dms_limit: dmsLimit ?? 0,
        dms_scheduled: tasks.filter((t) => t.action !== 'connect').length,
        tasks,
      },
    ]
  }, [days, tasks, invitesLimit, dmsLimit])

  const allTasks = useMemo(
    () => dayBuckets.flatMap((d) => d.tasks || []),
    [dayBuckets],
  )

  useEffect(() => {
    if (expandedId == null) return
    const stillThere = allTasks.some((t) => taskKey(t) === Number(expandedId))
    if (!stillThere) setExpandedId(null)
  }, [allTasks, expandedId])

  const q = query.trim().toLowerCase()
  const totalRaw = allTasks.length
  if (!totalRaw && !hiddenByCap) {
    return null
  }

  const showCaps = invitesLimit != null && dmsLimit != null

  function openOnly(prospectId) {
    const id = Number(prospectId)
    setExpandedId((cur) => (Number(cur) === id ? null : id))
  }

  function TaskCard({ task, mode, readOnly = false }) {
    const id = taskKey(task)
    const status = task.assist_status || 'suggested'
    const isReply = mode === 'reply'
    const busy = Number(busyProspectId) === id
    const expanded = Number(expandedId) === id
    void contactarTick
    const contactarDone = wasLiContactarDone(id)
    const hasMessage = Boolean((task.message || '').trim())
    const locked = readOnly || freeze

    return (
      <article
        className={[
          'flex flex-col rounded-lg border p-2 shadow-sm transition-shadow',
          isReply
            ? 'border-red-400/60 bg-red-50/50 ring-1 ring-red-300/40'
            : 'border-[#0A66C2]/30 bg-white ring-1 ring-[#0A66C2]/10',
          expanded ? 'shadow-md ring-2 ring-[#0A66C2]/35' : 'hover:shadow-md',
          isReply && expanded ? 'ring-red-400/60' : '',
          readOnly ? 'opacity-75' : '',
        ].join(' ')}
      >
        {readOnly ? (
          <p className="mb-1 text-[10px] font-medium text-zinc-500">Programado · aún no disponible hoy</p>
        ) : null}
        {isReply ? (
          <div className="mb-1">
            <span className="rounded-md bg-white/90 px-1.5 py-0.5 text-[10px] font-semibold text-red-800 ring-1 ring-red-200">
              Responder
            </span>
          </div>
        ) : null}

        <button
          type="button"
          className="flex w-full flex-col gap-0.5 text-left"
          onClick={() => openOnly(id)}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-[13px] font-semibold text-nx-ink">{task.prospect_name}</p>
              {task.company_name ? (
                <p className="truncate text-[10px] text-nx-muted">{task.company_name}</p>
              ) : null}
            </div>
            <span
              className={[
                'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
                linkedInAssistStatusClass(status),
              ].join(' ')}
            >
              {linkedInAssistStatusLabel(status)}
            </span>
          </div>
          {task.linkedin_url ? (
            <p className="truncate text-[10px] text-[#0A66C2]/80">{linkedInUrlLabel(task.linkedin_url)}</p>
          ) : null}
          {task.message ? (
            <p
              className={[
                'mt-0.5 text-[11px] leading-snug text-nx-muted',
                expanded ? 'whitespace-pre-wrap' : 'line-clamp-2',
              ].join(' ')}
            >
              {task.message}
            </p>
          ) : (
            <p className="mt-0.5 text-[11px] text-nx-muted">Preparando mensaje…</p>
          )}
        </button>

        <div className="mt-auto flex flex-col gap-1.5 pt-1.5">
          {!isReply ? (
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={locked || busy}
                className={[
                  'min-h-8 flex-1 rounded-lg border px-2.5 text-left text-[11px] font-semibold disabled:opacity-50',
                  contactarDone
                    ? 'border-zinc-200 bg-zinc-50 text-zinc-400 line-through decoration-zinc-400'
                    : 'border-[#0A66C2]/45 bg-white text-[#0A66C2] hover:bg-[#0A66C2]/5',
                ].join(' ')}
                onClick={() => onContactar?.(task)}
              >
                Contactar
              </button>
              <StepCheck
                done={contactarDone}
                title={contactarDone ? 'Perfil abierto' : 'Pendiente: abrir perfil'}
              />
            </div>
          ) : null}

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={locked || busy || (!hasMessage && !isReply)}
              className="min-h-8 flex-1 rounded-lg border border-[#0A66C2]/35 bg-[#0A66C2] px-2.5 text-left text-[11px] font-semibold text-white hover:bg-[#004182] disabled:opacity-50"
              onClick={() => {
                if (onEnviarMensaje) onEnviarMensaje(task)
                else if (isReply && onOpenLinkedIn) onOpenLinkedIn(task)
              }}
            >
              {busy ? '…' : isReply ? 'Responder' : 'Enviar mensaje'}
            </button>
            {!isReply ? (
              <StepCheck
                done={false}
                clickable
                disabled={locked || busy || !hasMessage}
                title="Marcar como enviado (sale de la cola)"
                onClick={() => onMarkSent?.(task)}
              />
            ) : (
              <StepCheck
                done={false}
                clickable
                disabled={locked || busy || !hasMessage}
                title="Marcar respuesta enviada"
                onClick={() => onMarkSent?.(task)}
              />
            )}
          </div>
        </div>

        {task.sequence_group ? (
          <p className="mt-1 text-[10px] text-nx-muted">{sequenceGroupLabel(task.sequence_group)}</p>
        ) : null}
      </article>
    )
  }

  function SectionDivider({ title, count, subtitle, urgent = false }) {
    return (
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3
            className={[
              'text-sm font-semibold',
              urgent ? 'text-red-800' : 'text-nx-ink',
            ].join(' ')}
          >
            {title}{' '}
            <span className={['tabular-nums', urgent ? 'text-red-600' : 'text-nx-muted'].join(' ')}>
              ({count})
            </span>
          </h3>
          {subtitle ? (
            <p className={['text-[11px]', urgent ? 'font-medium text-red-700/90' : 'text-nx-muted'].join(' ')}>
              {subtitle}
            </p>
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div className="w-full space-y-4">
      {showCaps ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-nx-muted">
          <span className="rounded-full bg-[#0A66C2]/10 px-2.5 py-1 font-medium text-[#0A66C2] ring-1 ring-[#0A66C2]/20">
            Conexiones hoy:{' '}
            <span className="font-semibold text-nx-ink">
              {Math.max(0, invitesLimit - invitesRemaining)}/{invitesLimit}
            </span>
          </span>
          <span className="rounded-full bg-nx-card-muted px-2.5 py-1 font-medium ring-1 ring-nx-border">
            Mensajes hoy:{' '}
            <span className="font-semibold text-nx-ink">
              {Math.max(0, dmsLimit - dmsRemaining)}/{dmsLimit}
            </span>
          </span>
          {hiddenByCap > 0 ? (
            <span className="rounded-full bg-zinc-50 px-2.5 py-1 font-semibold text-zinc-900 ring-1 ring-zinc-200">
              {hiddenByCap} programado{hiddenByCap === 1 ? '' : 's'} en días siguientes · límite diario
              para cuidar tu cuenta
            </span>
          ) : null}
        </div>
      ) : null}

      <p className="text-[11px] text-nx-muted">
        Contactar abre el perfil. Enviar mensaje copia y abre LinkedIn; la tilde gris confirma el envío.
        Si te responden, usá Respondieron y pegá su mensaje: Nexus arma el borrador y pausa la secuencia.
      </p>

      {totalRaw > 0 || q ? (
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Buscar en LinkedIn (nombre, empresa)…"
          className="w-full max-w-lg rounded-lg border border-nx-border bg-white px-2.5 py-1.5 text-[12px] text-nx-ink placeholder:text-nx-muted/70 focus:border-[#0A66C2]/45 focus:outline-none focus:ring-2 focus:ring-[#0A66C2]/15"
          aria-label="Buscar en cola LinkedIn"
        />
      ) : null}

      {dayBuckets.map((day) => {
        const dayTasks = (day.tasks || []).filter((t) => taskMatchesQuery(t, q))
        const replyTasks = dayTasks.filter((t) => Boolean(t.is_reply) || t.action === 'reply')
        const messageTasks = dayTasks.filter(
          (t) =>
            !Boolean(t.is_reply) &&
            t.action !== 'reply' &&
            (t.action === 'message' || t.action === 'connect' || t.action === 'verify_connect'),
        )
        if (!replyTasks.length && !messageTasks.length) return null
        const readOnly = day.actionable === false || Number(day.day_offset) > 0
        const inviteDetail =
          day.invites_limit > 0
            ? `Conexiones: ${day.invites_scheduled}/${day.invites_limit} · Mensajes: ${day.dms_scheduled}/${day.dms_limit}`
            : null
        return (
          <div key={`li-day-${day.day_offset}`} className="space-y-3">
            <QueueDayHeader
              label={day.label || (day.day_offset === 0 ? 'Hoy' : `Día +${day.day_offset}`)}
              scheduled={(day.invites_scheduled || 0) + (day.dms_scheduled || 0)}
              limit={(day.invites_limit || 0) + (day.dms_limit || 0) || null}
              actionable={!readOnly}
              detail={inviteDetail}
            />
            {replyTasks.length ? (
              <section>
                <SectionDivider
                  title="Responder"
                  count={replyTasks.length}
                  subtitle="Respondieron — actuar pronto"
                  urgent
                />
                <div className="mt-2 max-h-[min(22rem,45vh)] overflow-y-auto overscroll-contain pr-0.5">
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {replyTasks.map((task) => (
                      <TaskCard
                        key={`r-${day.day_offset}-${taskKey(task)}`}
                        task={task}
                        mode="reply"
                        readOnly={readOnly}
                      />
                    ))}
                  </div>
                </div>
              </section>
            ) : null}
            {messageTasks.length ? (
              <section>
                <SectionDivider
                  title="LinkedIn"
                  count={messageTasks.length}
                  subtitle={
                    readOnly
                      ? 'Visible para planificar · acciones habilitadas ese día'
                      : 'Contactar · Enviar · confirmar con la tilde'
                  }
                />
                <div className="mt-2 max-h-[min(28rem,50vh)] overflow-y-auto overscroll-contain pr-0.5">
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {messageTasks.map((task) => (
                      <TaskCard
                        key={`m-${day.day_offset}-${taskKey(task)}`}
                        task={task}
                        mode="message"
                        readOnly={readOnly}
                      />
                    ))}
                  </div>
                </div>
              </section>
            ) : null}
          </div>
        )
      })}

      {q && !allTasks.some((t) => taskMatchesQuery(t, q)) ? (
        <p className="rounded-lg border border-dashed border-[#0A66C2]/25 bg-white/80 px-3 py-3 text-center text-[12px] text-nx-muted">
          Sin coincidencias para “{query.trim()}”.
        </p>
      ) : null}
    </div>
  )
}
