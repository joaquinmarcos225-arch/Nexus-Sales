/** Encabezado de bucket diario en colas operativas. */
export function QueueDayHeader({
  label,
  scheduled = 0,
  limit = null,
  actionable = true,
  detail = null,
}) {
  return (
    <div
      className={[
        'flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2',
        actionable
          ? 'border-nx-border bg-white'
          : 'border-zinc-200 bg-zinc-50/80',
      ].join(' ')}
    >
      <div>
        <h4 className="text-[13px] font-semibold text-nx-ink">{label}</h4>
        {detail ? <p className="text-[11px] text-nx-muted">{detail}</p> : null}
        {!actionable ? (
          <p className="text-[11px] font-medium text-zinc-600">
            Programado · se habilita ese día para cuidar tu cuenta
          </p>
        ) : null}
      </div>
      {limit != null ? (
        <span className="rounded-full bg-nx-card-muted px-2.5 py-1 text-[11px] font-semibold tabular-nums text-nx-ink ring-1 ring-nx-border">
          {scheduled}/{limit}
        </span>
      ) : scheduled > 0 ? (
        <span className="rounded-full bg-nx-card-muted px-2.5 py-1 text-[11px] font-semibold tabular-nums text-nx-ink ring-1 ring-nx-border">
          {scheduled}
        </span>
      ) : null}
    </div>
  )
}
