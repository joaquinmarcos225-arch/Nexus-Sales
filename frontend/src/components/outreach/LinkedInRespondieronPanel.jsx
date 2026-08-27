import { useMemo, useState } from 'react'

/**
 * Estilo Outreach: el SDR registra que contestaron y pega el inbound.
 * Lista compacta + scroll + buscador (escala a ~200 sin comerse el viewport).
 */
export function LinkedInRespondieronPanel({
  prospects = [],
  freeze = false,
  busyProspectId = null,
  onOpenRespondieron,
  onHandoff,
}) {
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const rows = Array.isArray(prospects) ? prospects : []
    if (!q) return rows
    return rows.filter((p) => {
      const hay = [p.name, p.company_name, p.email, p.linkedin_url]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return hay.includes(q)
    })
  }, [prospects, query])

  if (!prospects.length) return null

  return (
    <section className="max-w-md space-y-2">
      <div className="border-b border-[#0A66C2]/15 pb-1.5">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-[13px] font-semibold tracking-tight text-nx-ink">
            ¿Te respondieron?
          </h3>
          <span className="shrink-0 text-[10px] tabular-nums text-nx-muted">
            {filtered.length}
            {filtered.length !== prospects.length ? ` / ${prospects.length}` : ''}
          </span>
        </div>
        <p className="mt-0.5 text-[11px] leading-snug text-nx-muted">
          Pegá el mensaje → Responder + pausa. Sale sola tras la reunión, si la secuencia sigue
          sin respuesta, o con Handoff (vos te encargás).
        </p>
      </div>

      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Buscar nombre o empresa…"
        className="w-full rounded-lg border border-nx-border bg-white px-2.5 py-1.5 text-[12px] text-nx-ink placeholder:text-nx-muted/70 focus:border-[#0A66C2]/45 focus:outline-none focus:ring-2 focus:ring-[#0A66C2]/15"
        aria-label="Buscar en Respondieron"
      />

      <ul className="max-h-[14rem] divide-y divide-nx-border/70 overflow-y-auto overscroll-contain rounded-lg border border-nx-border/90 bg-white">
        {filtered.length === 0 ? (
          <li className="px-2.5 py-3 text-center text-[11px] text-nx-muted">Sin coincidencias.</li>
        ) : (
          filtered.map((p) => {
            const id = Number(p.id)
            const busy = Number(busyProspectId) === id
            return (
              <li
                key={id}
                className="flex items-center gap-2 px-2.5 py-1.5 hover:bg-[#0A66C2]/[0.04]"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[12px] font-semibold leading-tight text-nx-ink">
                    {p.name}
                  </p>
                  {p.company_name ? (
                    <p className="truncate text-[10px] leading-tight text-nx-muted">
                      {p.company_name}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    disabled={freeze || busy}
                    className="rounded-md border border-[#0A66C2]/55 bg-white px-2 py-1 text-[10px] font-medium text-[#0A66C2] hover:bg-[#0A66C2]/5 disabled:opacity-45"
                    title="Pausá la secuencia y sacalo de esta lista. Te encargás vos en LinkedIn."
                    onClick={() => onHandoff?.(p)}
                  >
                    {busy ? '…' : 'Handoff'}
                  </button>
                  <button
                    type="button"
                    disabled={freeze || busy}
                    className="rounded-md bg-[#0A66C2] px-2 py-1 text-[10px] font-semibold text-white hover:bg-[#004182] disabled:opacity-45"
                    onClick={() => onOpenRespondieron?.(p)}
                  >
                    {busy ? '…' : 'Respondieron'}
                  </button>
                </div>
              </li>
            )
          })
        )}
      </ul>
    </section>
  )
}

/** Modal: pegar inbound → generar respuesta, o Handoff (manual). */
export function LinkedInRespondieronModal({
  prospect,
  busy = false,
  onClose,
  onSubmit,
  onHandoff,
}) {
  const [text, setText] = useState('')
  if (!prospect) return null

  const canSubmit = text.trim().length >= 2 && !busy

  return (
    <div className="fixed inset-0 z-[100] flex items-end justify-center bg-nx-ink/45 p-0 sm:items-center sm:p-4">
      <button type="button" className="absolute inset-0" aria-label="Cerrar" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        className="relative z-[101] flex max-h-[90vh] w-full max-w-md flex-col overflow-hidden rounded-t-2xl border border-nx-border bg-white shadow-2xl shadow-nx-ink/15 sm:rounded-2xl"
      >
        <div className="shrink-0 border-b border-nx-border px-4 py-3.5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#0A66C2]/90">
            LinkedIn · respuesta
          </p>
          <h2 className="mt-1 text-[15px] font-semibold text-nx-ink">
            ¿Qué te escribió {prospect.name || 'el prospecto'}?
          </h2>
          <p className="mt-1 text-[11px] leading-snug text-nx-muted">
            Pegá el mensaje para que Nexus arme el borrador y pause la secuencia. Si te encargás
            vos, usá Handoff. Cancelar solo cierra.
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3.5">
          <label className="block text-[11px] font-medium text-nx-muted" htmlFor="li-inbound-paste">
            Mensaje recibido
          </label>
          <textarea
            id="li-inbound-paste"
            autoFocus
            className="mt-1.5 min-h-[8rem] w-full resize-y rounded-xl border border-nx-border bg-nx-card-muted/40 px-3 py-2.5 text-sm leading-relaxed text-nx-ink placeholder:text-nx-muted/70 focus:border-[#0A66C2]/50 focus:outline-none focus:ring-2 focus:ring-[#0A66C2]/20"
            placeholder="Pegá acá lo que te contestaron en LinkedIn…"
            value={text}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
          />
        </div>

        <div className="flex shrink-0 flex-col-reverse gap-2 border-t border-nx-border bg-nx-card-muted/50 px-4 py-3.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
          <button
            type="button"
            className="rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-medium text-nx-ink hover:bg-nx-card-muted"
            onClick={onClose}
            disabled={busy}
          >
            Cancelar
          </button>
          <button
            type="button"
            className="rounded-lg border border-[#0A66C2]/55 bg-white px-3 py-2 text-xs font-medium text-[#0A66C2] hover:bg-[#0A66C2]/5 disabled:opacity-40"
            onClick={() => onHandoff?.()}
            disabled={busy}
            title="Sacá de esta lista y pausá la secuencia. Te encargás vos en LinkedIn."
          >
            Handoff
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            className="rounded-lg bg-[#0A66C2] px-3 py-2 text-xs font-semibold text-white hover:bg-[#004182] disabled:opacity-40"
            onClick={() => void onSubmit?.(text.trim())}
          >
            Generar respuesta
          </button>
        </div>
      </div>
    </div>
  )
}
