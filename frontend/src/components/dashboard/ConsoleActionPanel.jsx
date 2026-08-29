import { Link } from 'react-router-dom'

function ArrowIcon() {
  return (
    <svg className="size-4 shrink-0 text-nx-muted transition group-hover:translate-x-0.5 group-hover:text-nx-brand" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
      <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
    </svg>
  )
}

const PRIORITY_LABEL = ['Alta', 'Media', 'Normal', 'Baja']

/**
 * Panel de acción inmediata (columna derecha de la consola).
 * Ordena por prioridad y resalta la primera acción pendiente.
 */
export function ConsoleActionPanel({ todos = [] }) {
  function badgeClass(tone, count, urgent) {
    if (count <= 0) return 'bg-nx-bg text-nx-muted border border-nx-border'
    if (urgent) return 'bg-red-600 text-white'
    if (tone === 'linkedin') return 'bg-[#0A66C2]/15 text-[#0A66C2]'
    if (tone === 'whatsapp') return 'bg-[#25D366]/20 text-[#075E54]'
    if (tone === 'meeting') return 'bg-amber-100 text-amber-800'
    if (tone === 'alert') return 'bg-red-100 text-red-700'
    return 'bg-nx-brand/10 text-nx-brand'
  }

  const sorted = [...todos].sort((a, b) => {
    const pa = Number(a.priority) || 99
    const pb = Number(b.priority) || 99
    if (pa !== pb) return pa - pb
    return (Number(b.count) || 0) - (Number(a.count) || 0)
  })

  const firstPendingIdx = sorted.findIndex((t) => (Number(t.count) || 0) > 0)

  return (
    <aside className="flex flex-col gap-4">
      <section className="nx-card rounded-2xl border border-nx-border bg-nx-card p-4">
        <h3 className="text-sm font-semibold text-nx-ink">Requiere tu acción</h3>
        <p className="mt-1 text-[11px] text-nx-muted">Ordenado por prioridad — empezá por arriba.</p>

        {sorted.length === 0 ? (
          <p className="mt-3 rounded-lg border border-dashed border-nx-border bg-nx-bg/40 px-3 py-6 text-center text-xs text-nx-muted">
            Sin indicadores cargados.
          </p>
        ) : (
          <ul className="mt-3 space-y-1.5">
            {sorted.map((todo, idx) => {
              const count = Number(todo.count) || 0
              const urgent = idx === firstPendingIdx && count > 0
              const priorityIdx = Math.min(idx, PRIORITY_LABEL.length - 1)
              return (
                <li key={todo.id}>
                  <Link
                    to={todo.to}
                    className={[
                      'group flex items-center gap-3 rounded-lg border px-3 py-2.5 transition',
                      urgent
                        ? 'border-red-300/80 bg-red-50/80 hover:border-red-400 hover:bg-red-50'
                        : 'border-nx-border/70 bg-nx-bg/30 hover:border-nx-brand/40 hover:bg-nx-brand/5',
                    ].join(' ')}
                  >
                    <span className="flex w-10 shrink-0 flex-col items-center gap-0.5">
                      <span className="text-[9px] font-semibold uppercase tracking-wide text-nx-muted">
                        {count > 0 ? PRIORITY_LABEL[priorityIdx] : '—'}
                      </span>
                    </span>
                    <span className="flex-1 text-sm font-medium text-nx-ink">{todo.label}</span>
                    <span
                      className={[
                        'flex h-7 min-w-7 items-center justify-center rounded-md px-1.5 text-xs font-bold tabular-nums',
                        badgeClass(todo.tone, count, urgent),
                      ].join(' ')}
                    >
                      {count}
                    </span>
                    <ArrowIcon />
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section className="nx-card rounded-2xl border border-nx-border bg-nx-card p-4">
        <h3 className="text-sm font-semibold text-nx-ink">Accesos directos</h3>
        <div className="mt-3 grid grid-cols-1 gap-2">
          <Link to="/dashboard/responder" className="nx-btn nx-btn-ghost justify-center text-sm">
            Bandeja Responder
          </Link>
          <Link to="/campanas" className="nx-btn nx-btn-primary justify-center text-sm">
            + Nueva campaña
          </Link>
        </div>
      </section>
    </aside>
  )
}
