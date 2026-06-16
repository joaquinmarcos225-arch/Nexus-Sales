import { fmtDateTime, ownershipStatusLabel } from '../../utils/ownershipUi.js'

export function ActiveSequencesPanel({ sequences = [], loading = false, onOpenProspect }) {
  if (loading) {
    return (
      <div className="mb-6 rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-sm">
        <p className="text-sm text-[#6b7280]">Cargando secuencias activas...</p>
      </div>
    )
  }

  if (!sequences.length) {
    return null
  }

  return (
    <div className="mb-6 overflow-hidden rounded-xl border border-[#e5e7eb] bg-white shadow-sm shadow-[#111827]/5">
      <div className="border-b border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
        <h2 className="text-sm font-semibold text-[#111827]">Secuencias activas</h2>
        <p className="text-xs text-[#6b7280]">
          Cuentas en curso — día actual, próximo toque e historial al abrir detalle.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[720px] w-full text-sm">
          <thead className="text-left text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
            <tr className="border-b border-[#e5e7eb]">
              <th className="px-4 py-2">Prospecto</th>
              <th className="px-4 py-2">Empresa</th>
              <th className="px-4 py-2">Estado</th>
              <th className="px-4 py-2">Día actual</th>
              <th className="px-4 py-2">Próximo toque</th>
              <th className="px-4 py-2">Fecha próximo</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#e5e7eb] text-[#374151]">
            {sequences.map((row) => (
              <tr
                key={row.prospect_id}
                className="cursor-pointer hover:bg-[#f8fafc]/90"
                onClick={() => onOpenProspect?.(row.prospect_id)}
              >
                <td className="px-4 py-3 font-medium text-[#111827]">{row.prospect_name}</td>
                <td className="px-4 py-3">{row.company_name}</td>
                <td className="px-4 py-3">{ownershipStatusLabel(row.ownership_status)}</td>
                <td className="px-4 py-3">{row.current_day_label || '—'}</td>
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
