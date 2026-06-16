const STYLE = {
  draft: 'bg-slate-100 text-slate-700 border-slate-200',
  ready: 'bg-sky-50 text-sky-800 border-sky-200',
  running: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  paused: 'bg-amber-50 text-amber-900 border-amber-200',
  completed: 'bg-slate-900 text-white border-slate-900',
}

export const CAMPAIGN_STATUS_LABEL = {
  draft: 'Borrador',
  ready: 'Lista',
  running: 'En ejecución',
  paused: 'Pausada',
  completed: 'Completada',
}

export function CampaignStatusBadge({ status }) {
  const key = STYLE[status] ? status : 'draft'
  const cls =
    STYLE[key] ??
    'bg-slate-100 text-slate-700 border-slate-200'
  const label = CAMPAIGN_STATUS_LABEL[key] ?? status
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold capitalize ${cls}`}
    >
      {label}
    </span>
  )
}
