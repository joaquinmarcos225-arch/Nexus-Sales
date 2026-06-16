const STYLES = {
  imported: 'bg-slate-100 text-slate-800 border-slate-200',
  compatible: 'bg-emerald-50 text-emerald-900 border-emerald-200',
  not_compatible: 'bg-rose-50 text-rose-900 border-rose-200',
  contacted: 'bg-sky-50 text-sky-900 border-sky-200',
  replied: 'bg-indigo-50 text-indigo-900 border-indigo-200',
  interested: 'bg-lime-50 text-lime-900 border-lime-200',
  not_interested: 'bg-stone-100 text-stone-800 border-stone-200',
  meeting_booked: 'bg-violet-50 text-violet-900 border-violet-200',
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
