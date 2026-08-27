import { Link } from 'react-router-dom'

function ArrowIcon() {
  return (
    <svg className="size-4 shrink-0 text-nx-muted transition group-hover:translate-x-0.5 group-hover:text-nx-brand" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
      <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
    </svg>
  )
}

/**
 * Panel de acción inmediata (columna derecha de la consola).
 * Las filas se muestran siempre (también con count 0).
 *
 * @param {{
 *   todos: { id: string, label: string, count: number, to: string, tone?: 'alert' | 'default' | 'linkedin' | 'whatsapp' | 'call' | 'meeting' }[],
 * }} props
 */
export function ConsoleActionPanel({ todos = [] }) {
  function badgeClass(tone, count) {
    if (count <= 0) return 'bg-nx-bg text-nx-muted border border-nx-border'
    if (tone === 'linkedin') return 'bg-[#0A66C2]/15 text-[#0A66C2]'
    if (tone === 'whatsapp') return 'bg-[#25D366]/20 text-[#075E54]'
    if (tone === 'call') return 'bg-violet-100 text-violet-800'
    if (tone === 'meeting') return 'bg-amber-100 text-amber-800'
    if (tone === 'alert') return 'bg-red-100 text-red-700'
    return 'bg-nx-brand/10 text-nx-brand'
  }

  return (
    <aside className="flex flex-col gap-4">
      <section className="nx-card rounded-2xl border border-nx-border bg-nx-card p-4">
        <h3 className="text-sm font-semibold text-nx-ink">Requiere tu acción</h3>

        {todos.length === 0 ? (
          <p className="mt-3 rounded-lg border border-dashed border-nx-border bg-nx-bg/40 px-3 py-6 text-center text-xs text-nx-muted">
            Sin indicadores cargados.
          </p>
        ) : (
          <ul className="mt-3 space-y-1.5">
            {todos.map((todo) => {
              const count = Number(todo.count) || 0
              return (
                <li key={todo.id}>
                  <Link
                    to={todo.to}
                    className="group flex items-center gap-3 rounded-lg border border-nx-border/70 bg-nx-bg/30 px-3 py-2.5 transition hover:border-nx-brand/40 hover:bg-nx-brand/5"
                  >
                    <span className="flex-1 text-sm font-medium text-nx-ink">{todo.label}</span>
                    <span
                      className={[
                        'flex h-7 min-w-7 items-center justify-center rounded-md px-1.5 text-xs font-bold tabular-nums',
                        badgeClass(todo.tone, count),
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
          <Link to="/campanas" className="nx-btn nx-btn-primary justify-center text-sm">
            + Nueva campaña
          </Link>
        </div>
      </section>
    </aside>
  )
}
