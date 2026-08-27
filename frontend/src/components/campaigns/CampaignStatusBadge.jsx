const STYLE = {
  draft: 'bg-zinc-100 text-zinc-700 border-zinc-200',
  ready: 'bg-zinc-100 text-zinc-800 border-zinc-200',
  running: 'bg-red-50 text-red-800 border-red-200',
  paused: 'bg-zinc-100 text-zinc-800 border-zinc-300',
  completed: 'bg-zinc-900 text-white border-zinc-900',
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
    'bg-zinc-100 text-zinc-700 border-zinc-200'
  const label = CAMPAIGN_STATUS_LABEL[key] ?? status
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold capitalize ${cls}`}
    >
      {label}
    </span>
  )
}
