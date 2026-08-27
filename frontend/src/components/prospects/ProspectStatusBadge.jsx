const STYLES = {
  imported: 'bg-zinc-100 text-zinc-800 border-zinc-200',
  compatible: 'bg-red-50 text-red-900 border-red-200',
  not_compatible: 'bg-red-50 text-red-900 border-red-200',
  contacted: 'bg-zinc-100 text-zinc-800 border-zinc-200',
  replied: 'bg-red-50 text-red-800 border-red-200',
  interested: 'bg-red-50 text-red-900 border-red-200',
  not_interested: 'bg-zinc-100 text-zinc-800 border-zinc-200',
  meeting_booked: 'bg-zinc-900 text-white border-zinc-900',
  failed: 'bg-red-100 text-red-900 border-red-200',
}

const LABELS = {
  imported: 'Importado',
  compatible: 'Compatible',
  not_compatible: 'No compatible',
  contacted: 'Contactado',
  replied: 'Respondió',
  interested: 'Interesado',
  not_interested: 'No interesado',
  meeting_booked: 'Reunión agendada',
  failed: 'Falló',
}

export function ProspectStatusBadge({ status }) {
  const cls = STYLES[status] ?? STYLES.imported
  return (
    <span
      className={`inline-flex max-w-full truncate rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}
    >
      {LABELS[status] ?? status}
    </span>
  )
}
