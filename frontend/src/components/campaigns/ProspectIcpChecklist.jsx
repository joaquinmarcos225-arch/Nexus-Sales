function criterionText(item) {
  if (item.state === 'mismatch') return `${item.label}: no coincide`
  return `${item.label}: coincide`
}

export function ProspectIcpChecklist({ prospect, className = '' }) {
  const items = Array.isArray(prospect?.icp_checklist) ? prospect.icp_checklist : []
  if (items.length === 0) return null

  return (
    <div
      className={`mt-1.5 flex max-w-3xl flex-wrap gap-1.5 ${className}`.trim()}
      aria-label="Coincidencia con el ICP"
    >
      {items.map((item) => {
        const mismatch = item.state === 'mismatch'
        const title = [
          `Buscado: ${item.target || '—'}`,
          `Encontrado: ${item.actual || '—'}`,
          item.reason || '',
        ]
          .filter(Boolean)
          .join(' · ')

        return (
          <span
            key={item.key}
            title={title}
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
              mismatch
                ? 'border-red-300 bg-red-50 text-red-800'
                : 'border-emerald-300 bg-emerald-50 text-emerald-800'
            }`}
          >
            <span aria-hidden="true">{mismatch ? '✕' : '✓'}</span>
            <span>{criterionText(item)}</span>
          </span>
        )
      })}
    </div>
  )
}
