import { hasRealLinkedInUrl, linkedInOpenUrl } from '../../utils/linkedinAssist.js'

function OutreachReadyBadge({ ready, missingFields }) {
  if (ready) {
    return (
      <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold text-red-900 ring-1 ring-red-100">
        Sí
      </span>
    )
  }
  const hint = Array.isArray(missingFields) && missingFields.length ? missingFields.join(', ') : 'incompleto'
  return (
    <span
      className="rounded bg-zinc-50 px-1.5 py-0.5 text-[10px] font-medium text-zinc-950 ring-1 ring-zinc-100"
      title={hint}
    >
      No
    </span>
  )
}

function LinkedInCell({ url, valid }) {
  const openUrl = linkedInOpenUrl(url)
  if (!openUrl) {
    return <span className="text-zinc-400">—</span>
  }
  return (
    <a
      href={openUrl}
      target="_blank"
      rel="noreferrer"
      className={`block max-w-[16rem] break-all text-[11px] font-medium hover:underline ${
        valid || hasRealLinkedInUrl(url) ? 'text-zinc-800' : 'text-zinc-800'
      }`}
      title={openUrl}
    >
      {openUrl}
    </a>
  )
}

function ProspeoPhoneInfoPanel({ info }) {
  if (!info || typeof info !== 'object') return null
  return (
    <details className="mt-2 rounded-lg border border-zinc-200 bg-zinc-50/80 p-3 text-[11px] text-zinc-700">
      <summary className="cursor-pointer font-semibold text-zinc-800">
        Teléfonos Prospeo (plan y endpoints)
      </summary>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        <li>
          <span className="font-medium">search-person:</span> campo{' '}
          <code className="text-[10px]">mobile</code> — número{' '}
          {info.search_person_reveals_number_without_enrich || 'variable'} sin enrich.
        </li>
        <li>
          <span className="font-medium">enrich-person:</span>{' '}
          <code className="break-all text-[10px]">{info.enrich_person_endpoint}</code> con{' '}
          <code className="text-[10px]">enrich_mobile=true</code> revela móvil (créditos).
        </li>
        {info.batch_mode_note ? <li>{info.batch_mode_note}</li> : null}
        {info.if_empty_phone ? <li>{info.if_empty_phone}</li> : null}
        {info.whatsapp ? <li>{info.whatsapp}</li> : null}
      </ul>
    </details>
  )
}

/**
 * Tabla de prospección Nexus: persona + canales + listo outreach.
 */
export function ProspectingLeadsTable({ rows = [], phoneInfo = null, selectedId = '', onSelectRow }) {
  const list = Array.isArray(rows) ? rows.filter((r) => r && typeof r === 'object') : []
  const readyCount = list.filter((r) => r.outreach_ready).length

  if (!list.length) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-zinc-200 bg-zinc-50/60 p-4">
        <p className="text-xs font-semibold text-zinc-800">Prospección — contactos Nexus</p>
        <p className="mt-1 text-[11px] text-zinc-600">
          Todavía no hay contactos. La búsqueda automática los cargará acá.
        </p>
        <ProspeoPhoneInfoPanel info={phoneInfo} />
      </div>
    )
  }

  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-zinc-200/80 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-100 bg-zinc-50/50 px-3 py-2">
        <span className="text-xs font-semibold text-zinc-950">Prospección — leads Nexus</span>
        <span className="text-[10px] font-medium text-zinc-900">
          {list.length} contactos · {readyCount} listos outreach
        </span>
      </div>
      <p className="border-b border-zinc-100 px-3 py-1.5 text-[10px] text-zinc-600">
        Outreach Ready = persona real + email corporativo (dominio empresa) + LinkedIn personal (/in/…).
        Teléfono, WhatsApp y score ICP no bloquean.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-zinc-100 bg-zinc-50/80 text-[10px] font-bold uppercase tracking-wide text-zinc-500">
              <th className="px-3 py-2">Nombre</th>
              <th className="px-3 py-2">Cargo</th>
              <th className="px-3 py-2">Email</th>
              <th className="px-3 py-2">Celular</th>
              <th className="px-3 py-2">WhatsApp</th>
              <th className="px-3 py-2">LinkedIn</th>
              <th className="px-3 py-2 text-center">Outreach Ready</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {list.map((row) => {
              const id = row.external_id
              const selected = selectedId === id
              return (
                <tr
                  key={id}
                  className={`cursor-pointer hover:bg-zinc-50/40 ${selected ? 'bg-zinc-50/60' : ''}`}
                  onClick={() => onSelectRow?.(id)}
                >
                  <td className="px-3 py-2 font-semibold text-zinc-900">{row.person_name}</td>
                  <td className="max-w-[10rem] truncate px-3 py-2 text-zinc-600" title={row.role || ''}>
                    {row.role || '—'}
                  </td>
                  <td className="max-w-[12rem] break-all px-3 py-2 text-zinc-700" title={row.email || ''}>
                    {row.email || '—'}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-zinc-700">
                    {row.phone ? (
                      <span title={row.phone_source || undefined}>{row.phone}</span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-zinc-700">
                    {row.whatsapp_number || row.whatsapp || '—'}
                  </td>
                  <td className="px-3 py-2">
                    <LinkedInCell url={row.linkedin_url} valid={row.linkedin_valid} />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <OutreachReadyBadge ready={row.outreach_ready} missingFields={row.missing_fields} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <ProspeoPhoneInfoPanel info={phoneInfo} />
    </div>
  )
}
