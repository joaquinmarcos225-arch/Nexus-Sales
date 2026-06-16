const SOURCE_LABELS = {
  own_website: 'Web propia',
  prospeo: 'Prospeo',
  web_search: 'Búsqueda web',
  domain_guess: 'Inferencia dominio',
  doubtful: 'Dominio dudoso',
  unresolved: 'Sin resolver',
}

function sourceLabel(key) {
  if (!key) return '—'
  return SOURCE_LABELS[key] || key
}

export function MvpCompanyDomainsTable({ companies = [], metrics }) {
  const rows = companies.filter((c) => (c.result_kind || 'company') === 'company')
  if (!rows.length) return null

  const m = metrics || {}
  const found = m.companies_found ?? rows.length
  const verifiedCount =
    m.domains_resolved ??
    rows.filter((c) => (c.domain_trust || '') === 'verified').length
  const doubtfulCount = rows.filter((c) => (c.domain_trust || '') === 'doubtful').length
  const rate = m.domain_resolution_rate_pct ?? (found ? Math.round((100 * verifiedCount) / found) : 0)

  return (
    <div className="mt-4 rounded-xl border border-sky-200 bg-sky-50/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-sky-950">Dominios corporativos (antes de Prospeo)</p>
        <p className="text-[10px] font-medium text-sky-900">
          {verifiedCount}/{found} verificados
          {doubtfulCount > 0 ? ` · ${doubtfulCount} dudosos` : ''} · {rate}%
        </p>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-3">
        <MiniMetric label="Empresas" value={found} />
        <MiniMetric label="Verificados" value={verifiedCount} />
        <MiniMetric label="Tasa" value={`${rate}%`} />
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[680px] text-left text-[11px]">
          <thead>
            <tr className="border-b border-sky-200 text-[10px] font-bold uppercase text-zinc-500">
              <th className="px-2 py-1.5">Empresa</th>
              <th className="px-2 py-1.5">Website</th>
              <th className="px-2 py-1.5">Dominio</th>
              <th className="px-2 py-1.5">Fuente dominio</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const trust = c.domain_trust || (c.domain_source === 'doubtful' ? 'doubtful' : null)
              const ok = trust === 'verified'
              const doubtful = trust === 'doubtful'
              return (
                <tr key={c.external_id} className="border-b border-sky-100/80">
                  <td className="px-2 py-1.5 font-medium text-zinc-900">{c.name}</td>
                  <td className="max-w-[200px] truncate px-2 py-1.5 text-zinc-700">
                    {c.website_url ? (
                      <a
                        href={c.website_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sky-800 hover:underline"
                      >
                        {c.website_url.replace(/^https?:\/\//, '')}
                      </a>
                    ) : (
                      '—'
                    )}
                    {c.source_directory_url ? (
                      <span className="mt-0.5 block text-[9px] text-amber-800">
                        Dir: {c.source_directory_url.replace(/^https?:\/\//, '').slice(0, 48)}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-2 py-1.5">
                    {c.company_domain ? (
                      <span
                        className={
                          doubtful
                            ? 'font-semibold text-rose-800 line-through'
                            : 'font-semibold text-emerald-800'
                        }
                      >
                        {c.company_domain}
                      </span>
                    ) : (
                      <span className="font-medium text-amber-800">Sin dominio</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    <span
                      className={
                        ok
                          ? 'rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-900'
                          : doubtful
                            ? 'rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-900'
                            : 'rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900'
                      }
                    >
                      {sourceLabel(c.domain_source)}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {verifiedCount < found ? (
        <p className="mt-2 text-[10px] text-amber-900">
          Sin dominio corporativo no se ejecuta Prospeo. Usá «Enriquecer contactos (Prospeo)» para
          resolver hasta 5 dominios por corrida (8s c/u). Requiere BRAVE_SEARCH_API_KEY.
        </p>
      ) : null}
    </div>
  )
}

function MiniMetric({ label, value }) {
  return (
    <div className="rounded-lg border border-white bg-white px-2 py-1.5 text-center shadow-sm">
      <p className="text-[10px] font-medium text-zinc-500">{label}</p>
      <p className="text-sm font-bold text-sky-900">{value}</p>
    </div>
  )
}
