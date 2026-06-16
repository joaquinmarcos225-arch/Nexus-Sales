import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import {
  linkedInAssistStatusClass,
  linkedInAssistStatusLabel,
  linkedInPriorityClass,
  linkedInUrlLabel,
} from '../../utils/linkedinAssist.js'
import { sequenceGroupLabel } from '../../utils/sequenceUi.js'

/**
 * Cola operativa SDR — LinkedIn Assisted Layer (MVP).
 * Nexus prepara; el humano envía con Enter en LinkedIn.
 */
export function LinkedInAssistQueue({
  tasks = [],
  freeze = false,
  busyProspectId = null,
  onOpenLinkedIn,
  onMarkSent,
}) {
  if (!tasks.length) {
    return null
  }

  return (
    <div className="rounded-xl border border-rose-100/90 bg-gradient-to-br from-zinc-50 via-white to-rose-50/30 p-4 shadow-md shadow-zinc-900/5 ring-1 ring-zinc-900/5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#0A66C2] text-xs font-bold text-white shadow-sm">
            in
          </span>
          <div>
            <h3 className="text-sm font-semibold text-zinc-900">Cola LinkedIn · Copilot SDR</h3>
            <p className="mt-0.5 text-xs text-zinc-600">
              Nexus genera y prepara; vos revisás y enviás en LinkedIn. Confirmá solo después del envío real.
            </p>
          </div>
        </div>
        <span className="rounded-full bg-nx-brand px-3 py-1 text-xs font-bold text-white shadow-sm shadow-rose-900/20">
          {tasks.length} pendiente{tasks.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {tasks.map((task) => {
          const status = task.assist_status || 'suggested'
          const isOpened = status === 'opened'
          return (
            <article
              key={task.prospect_id}
              className="flex flex-col rounded-xl border border-zinc-200/80 bg-white p-4 shadow-sm ring-1 ring-zinc-900/5"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-zinc-900">{task.prospect_name}</p>
                  <p className="text-xs text-zinc-500">{task.company_name || '—'}</p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${linkedInPriorityClass(task.priority)}`}
                  >
                    {task.priority || 'media'}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${linkedInAssistStatusClass(status)}`}
                  >
                    {linkedInAssistStatusLabel(status)}
                  </span>
                </div>
              </div>

              <p className="mt-2 text-[11px] font-medium text-zinc-500">
                {linkedInUrlLabel(task.linkedin_url)}
                {task.sequence_group ? (
                  <span className="text-zinc-400"> · {sequenceGroupLabel(task.sequence_group)}</span>
                ) : null}
              </p>

              {isOpened ? (
                <p className="mt-1.5 rounded-md bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-900 ring-1 ring-amber-100">
                  Perfil abierto y mensaje en portapapeles — pendiente de confirmación manual.
                </p>
              ) : null}

              <p className="mt-2 line-clamp-4 flex-1 rounded-lg bg-zinc-50 p-2.5 text-xs leading-relaxed text-zinc-700">
                {(task.message || '').slice(0, 360)}
                {(task.message || '').length > 360 ? '…' : ''}
              </p>

              <PremiumGradientButton
                fullWidth
                className="mt-3"
                disabled={freeze || busyProspectId === task.prospect_id}
                onClick={() => onOpenLinkedIn?.(task)}
              >
                {busyProspectId === task.prospect_id ? 'Abriendo…' : 'Abrir LinkedIn'}
              </PremiumGradientButton>

              <button
                type="button"
                disabled={freeze || busyProspectId === task.prospect_id}
                className="mt-2 w-full rounded-lg border border-emerald-200 bg-emerald-50/80 py-2 text-xs font-semibold text-emerald-900 hover:bg-emerald-100 disabled:opacity-40"
                onClick={() => onMarkSent?.(task)}
              >
                Marcar como enviado (manual)
              </button>

              <p className="mt-2 text-center text-[10px] leading-snug text-zinc-400">
                Abrir LinkedIn no cuenta como enviado. La extensión futura podrá detectar el envío automáticamente.
              </p>
            </article>
          )
        })}
      </div>
    </div>
  )
}
