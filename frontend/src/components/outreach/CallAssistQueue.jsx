import { useEffect, useMemo, useState } from 'react'
import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import { sequenceGroupLabel } from '../../utils/sequenceUi.js'
import { copyTextToClipboard } from '../../utils/linkedinAssist.js'
import { CallDialerModal } from './CallDialerModal.jsx'

function taskKey(task) {
  return Number(task?.prospect_id)
}

function phoneKindLabel(kind) {
  if (kind === 'landline') return 'Fijo'
  if (kind === 'mobile') return 'Celular'
  return 'Teléfono'
}

function phoneKindClass(kind) {
  if (kind === 'landline') return 'bg-violet-100 text-violet-800 ring-violet-200'
  if (kind === 'mobile') return 'bg-emerald-100 text-emerald-800 ring-emerald-200'
  return 'bg-zinc-100 text-zinc-700 ring-zinc-200'
}

/**
 * Cola operativa SDR — llamadas asistidas (estilo tareas Call de Outreach).
 */
export function CallAssistQueue({
  tasks = [],
  freeze = false,
  busyProspectId = null,
  sequenceHasCall = false,
  onMarkDone,
}) {
  const [expandedId, setExpandedId] = useState(null)
  const [dialerTask, setDialerTask] = useState(null)

  const allTasks = useMemo(() => (Array.isArray(tasks) ? tasks : []), [tasks])

  useEffect(() => {
    if (expandedId == null) return
    const stillThere = allTasks.some((t) => taskKey(t) === Number(expandedId))
    if (!stillThere) setExpandedId(null)
  }, [allTasks, expandedId])

  if (!allTasks.length) {
    return (
      <div className="rounded-lg border border-dashed border-violet-300/50 bg-violet-50/40 px-3 py-4 text-center text-[12px] text-nx-muted">
        {sequenceHasCall ? (
          <>
            Sin llamadas pendientes hoy. Cuando llegue un toque de <strong>Llamada</strong> en la secuencia,
            acá aparece el prospecto con número (fijo o celular) y el guion.
          </>
        ) : (
          <>
            Esta campaña no tiene toques de <strong>Llamada</strong> en la secuencia. Editá la campaña →
            plantilla de secuencia → agregá el canal <strong>Llamada</strong> en algún día (ej. día 7).
          </>
        )}
      </div>
    )
  }

  function openDialer(task) {
    setDialerTask(task)
  }

  function TaskCard({ task }) {
    const id = taskKey(task)
    const busy = Number(busyProspectId) === id
    const expanded = Number(expandedId) === id
    const locked = freeze

    return (
      <article
        className={[
          'flex flex-col rounded-xl border p-2.5 shadow-sm transition-shadow',
          'min-h-[7.5rem]',
          'border-violet-300/60 bg-white ring-1 ring-violet-200/40',
          expanded ? 'shadow-md ring-2 ring-violet-300/50' : 'hover:shadow-md',
        ].join(' ')}
      >
        <button
          type="button"
          className="flex w-full items-start justify-between gap-1.5 text-left"
          aria-expanded={expanded}
          onClick={() => setExpandedId((cur) => (Number(cur) === id ? null : id))}
        >
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold leading-tight text-nx-ink">
              {task.prospect_name}
            </p>
            {task.company_name ? (
              <p className="truncate text-[11px] text-nx-muted">{task.company_name}</p>
            ) : null}
          </div>
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
        </button>

        <p className="mt-2 text-[11px] font-semibold text-violet-900">
          Tenés que llamar a este número hoy
        </p>

        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${phoneKindClass(task.phone_kind)}`}
          >
            {phoneKindLabel(task.phone_kind)}
          </span>
          <span className="text-[12px] font-semibold tabular-nums text-nx-ink">
            {task.phone_display || task.phone_digits || '—'}
          </span>
          {task.sequence_group ? (
            <span className="text-[10px] text-nx-muted">{sequenceGroupLabel(task.sequence_group)}</span>
          ) : null}
        </div>

        {expanded ? (
          <div className="mt-2 space-y-2 border-t border-violet-100 pt-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-nx-muted">Guion</p>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-violet-50/60 p-2 text-[11px] leading-relaxed text-nx-ink">
              {task.brief || '—'}
            </pre>
            <div className="flex flex-wrap gap-2">
              <PremiumGradientButton
                type="button"
                size="sm"
                disabled={locked}
                onClick={() => openDialer(task)}
              >
                Abrir marcador
              </PremiumGradientButton>
              <button
                type="button"
                disabled={locked || !task.brief}
                className="rounded-lg border border-nx-border bg-white px-3 py-1.5 text-[11px] font-medium text-nx-ink hover:bg-nx-card-muted disabled:opacity-50"
                onClick={() => void copyTextToClipboard(task.brief || '')}
              >
                Copiar guion
              </button>
              <button
                type="button"
                disabled={locked || busy}
                className="rounded-lg border border-nx-border bg-white px-3 py-1.5 text-[11px] font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-50"
                onClick={() => onMarkDone?.(task)}
              >
                {busy ? 'Guardando…' : 'Marcar como hecha'}
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-2 flex flex-wrap gap-2">
            <PremiumGradientButton type="button" size="sm" disabled={locked} onClick={() => openDialer(task)}>
              Llamar
            </PremiumGradientButton>
            <p className="flex-1 line-clamp-2 text-[11px] text-nx-muted">{task.brief || 'Sin guion'}</p>
          </div>
        )}
      </article>
    )
  }

  return (
    <>
      <div className="space-y-2">
        <p className="text-[11px] text-nx-muted">
          {allTasks.length} llamada{allTasks.length === 1 ? '' : 's'} pendiente{allTasks.length === 1 ? '' : 's'} ·
          preferimos fijo si Prospeo lo trajo
        </p>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {allTasks.map((task) => (
            <TaskCard key={taskKey(task)} task={task} />
          ))}
        </div>
      </div>

      <CallDialerModal
        open={Boolean(dialerTask)}
        task={dialerTask}
        busy={Number(busyProspectId) === taskKey(dialerTask)}
        onClose={() => setDialerTask(null)}
        onMarkDone={(t) => {
          onMarkDone?.(t)
          setDialerTask(null)
        }}
      />
    </>
  )
}
