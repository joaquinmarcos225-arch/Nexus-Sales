import { fmtDateTime, ownershipStatusLabel } from '../../utils/ownershipUi.js'

export function ActiveSequencesPanel({ sequences = [], loading = false, onOpenProspect }) {
  if (loading) {
    return (
      <div className="mb-6 rounded-xl border border-nx-border bg-white p-4 shadow-sm">
        <p className="text-sm text-nx-muted">Cargando secuencias activas...</p>
      </div>
    )
  }

  if (!sequences.length) {
    return null
  }

  return (
    <div className="mb-6 overflow-hidden rounded-xl border border-nx-border bg-white shadow-sm shadow-nx-ink/5">
      <div className="border-b border-nx-border bg-nx-card-muted px-4 py-3">
        <h2 className="text-sm font-semibold text-nx-ink">Secuencias activas</h2>
        <p className="text-xs text-nx-muted">
          Cuentas en curso — día actual, próximo toque e historial al abrir detalle.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[720px] w-full text-sm">
          <thead className="text-left text-xs font-semibold uppercase tracking-wide text-nx-muted">
            <tr className="border-b border-nx-border">
              <th className="px-4 py-2">Prospecto</th>
              <th className="px-4 py-2">Empresa</th>
              <th className="px-4 py-2">Estado</th>
              <th className="px-4 py-2">Último hito</th>
              <th className="px-4 py-2">Próximo toque</th>
              <th className="px-4 py-2">Fecha próximo</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-nx-border text-nx-ink">
            {sequences.map((row) => (
              <tr
                key={row.prospect_id}
                className="cursor-pointer hover:bg-nx-card-muted/90"
                onClick={() => onOpenProspect?.(row.prospect_id)}
              >
                <td className="px-4 py-3 font-medium text-nx-ink">{row.prospect_name}</td>
                <td className="px-4 py-3">{row.company_name}</td>
                <td className="px-4 py-3">{ownershipStatusLabel(row.ownership_status)}</td>
                <td className="px-4 py-3">{row.last_completed_day_label || row.current_day_label || '—'}</td>
                <td className="px-4 py-3">{row.next_touch_label || '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap">{fmtDateTime(row.next_touch_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
