export function MvpContactsTable({ rows = [], metrics, onSelectContact, selectedId }) {
  const m = metrics || {}
  const realRows = rows.filter(
    (r) =>
      r.status_message === 'Contacto real' ||
      (r.contact_external_id && r.status_message !== 'Email genérico / no verificado'),
  )
  const genericRows = rows.filter((r) => r.status_message === 'Email genérico / no verificado')
  const emptyRows = rows.filter(
    (r) =>
      !r.contact_external_id &&
      r.status_message &&
      r.status_message !== 'Contacto real' &&
      r.status_message !== 'Email genérico / no verificado',
  )

  return (
    <div className="mt-4 space-y-3">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50/30 p-3">
        <p className="text-xs font-semibold text-emerald-950">Contactos por empresa</p>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Metric label="Empresas" value={m.companies_found ?? 0} />
          <Metric label="Personas reales" value={m.contacts_found ?? 0} />
          <Metric label="Emails genéricos" value={m.generic_emails_found ?? 0} />
          <Metric label="Emails total" value={m.emails_found ?? 0} />
          <Metric label="Listos outreach" value={m.contacts_ready_outreach ?? 0} />
        </div>
      </div>

      {realRows.length > 0 ? (
        <ContactSection
          title="Contactos reales"
          borderClass="border-emerald-200"
          bgClass="bg-emerald-50/30"
          rows={realRows}
          onSelectContact={onSelectContact}
          selectedId={selectedId}
        />
      ) : null}

      {genericRows.length > 0 ? (
        <ContactSection
          title="Emails genéricos (no verificados)"
          borderClass="border-amber-200"
          bgClass="bg-amber-50/30"
          rows={genericRows}
          onSelectContact={onSelectContact}
          selectedId={selectedId}
          generic
        />
      ) : null}

      {emptyRows.length > 0 ? (
        <ContactSection
          title="Sin contacto"
          borderClass="border-zinc-200"
          bgClass="bg-zinc-50/80"
          rows={emptyRows}
          statusOnly
        />
      ) : null}

      {!rows.length ? (
        <p className="text-center text-xs text-zinc-500">
          Ejecutá «Enriquecer contactos (Prospeo)» para buscar personas o emails genéricos por empresa.
        </p>
      ) : null}
    </div>
  )
}

function ContactSection({
  title,
  borderClass,
  bgClass,
  rows,
  onSelectContact,
  selectedId,
  generic = false,
  statusOnly = false,
}) {
  return (
    <div className={`rounded-xl border ${borderClass} ${bgClass} p-3`}>
      <p className="text-xs font-semibold text-zinc-900">{title}</p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-[11px]">
          <thead>
            <tr className={`border-b ${borderClass} text-[10px] font-bold uppercase text-zinc-500`}>
              <th className="px-2 py-1.5">Empresa</th>
              <th className="px-2 py-1.5">{statusOnly ? 'Estado' : generic ? 'Email' : 'Persona'}</th>
              {!statusOnly ? <th className="px-2 py-1.5">Cargo</th> : null}
              {!statusOnly ? <th className="px-2 py-1.5">Email</th> : null}
              {!statusOnly && !generic ? <th className="px-2 py-1.5">LinkedIn</th> : null}
              {!statusOnly ? <th className="px-2 py-1.5">Conf.</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => {
              const key = r.contact_external_id || `${r.company_external_id}-${idx}`
              const isStatus = statusOnly || !r.contact_external_id
              return (
                <tr key={key} className={`border-b ${borderClass}/80`}>
                  <td className="px-2 py-1.5 font-medium text-zinc-900">{r.company_name}</td>
                  <td className="px-2 py-1.5">
                    {isStatus ? (
                      <span className="font-medium text-amber-800">{r.status_message}</span>
                    ) : r.contact_external_id ? (
                      <button
                        type="button"
                        className="font-semibold text-violet-800 hover:underline"
                        onClick={() => onSelectContact?.(r.contact_external_id)}
                      >
                        {r.person_name}
                      </button>
                    ) : (
                      r.person_name || '—'
                    )}
                  </td>
                  {!statusOnly ? (
                    <>
                      <td className="px-2 py-1.5 text-zinc-700">{r.role || '—'}</td>
                      <td className="px-2 py-1.5 text-zinc-700">{r.email || '—'}</td>
                      {!generic ? (
                        <td className="px-2 py-1.5">
                          {r.linkedin_url ? (
                            <a
                              href={r.linkedin_url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-semibold text-sky-700"
                            >
                              Perfil
                            </a>
                          ) : (
                            '—'
                          )}
                        </td>
                      ) : null}
                      <td className="px-2 py-1.5 text-zinc-600">
                        {r.confidence != null ? `${r.confidence}%` : '—'}
                      </td>
                    </>
                  ) : null}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Metric({ label, value }) {
  return (
    <div className="rounded-lg border border-white bg-white px-2 py-1.5 text-center shadow-sm">
      <p className="text-[10px] font-medium text-zinc-500">{label}</p>
      <p className="text-sm font-bold text-emerald-900">{value}</p>
    </div>
  )
}

/** Diagnóstico search-person Prospeo por empresa (búsqueda vs filtros). */
export function ProspeoSearchDebugPanel({ rows = [] }) {
  if (!rows.length) return null
  return (
    <details className="mt-3 rounded-lg border border-violet-200 bg-violet-50/40 p-3" open>
      <summary className="cursor-pointer text-xs font-semibold text-violet-950">
        Diagnóstico Prospeo — {rows.length} empresa(s)
      </summary>
      <div className="mt-2 max-h-80 overflow-auto">
        <table className="w-full min-w-[800px] text-left text-[10px]">
          <thead>
            <tr className="border-b border-violet-200 font-bold uppercase text-zinc-500">
              <th className="px-1.5 py-1">Empresa</th>
              <th className="px-1.5 py-1">Dominio enviado</th>
              <th className="px-1.5 py-1">Requests</th>
              <th className="px-1.5 py-1">Resultados Prospeo</th>
              <th className="px-1.5 py-1">Válidos</th>
              <th className="px-1.5 py-1">Descartados</th>
              <th className="px-1.5 py-1">Código</th>
              <th className="px-1.5 py-1">Motivo / estado</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const reqs = Array.isArray(r.requests) ? r.requests : []
              const blockedCodes = new Set([
                'INSUFFICIENT_CREDITS',
                'RATE_LIMITED',
                'PLAN_REQUIRED',
                'INVALID_API_KEY',
                'INTEGRATION_ERROR',
              ])
              const reqSummary = r.request_executed
                ? reqs
                    .map((q) => {
                      const code = q.error_code || ''
                      const alert =
                        code &&
                        (blockedCodes.has(code) ||
                          code.startsWith('HTTP_') && !String(code).includes('200'))
                          ? `!${code}`
                          : code && code !== 'NO_RESULTS'
                            ? `(${code})`
                            : ''
                      return `${q.request_type}:${q.results_count ?? 0}${alert}`
                    })
                    .join(' · ') || 'sí'
                : 'no'
              const broadReq = reqs.find((q) => q.request_type === 'broad')
              const blocked = r.search_blocked === true
              const zero = (r.prospeo_results ?? 0) === 0 && !blocked
              const resultsLabel = blocked
                ? '—'
                : zero
                  ? '0'
                  : String(r.prospeo_results ?? 0)
              return (
                <tr key={`${r.company_name}-${i}`} className="border-b border-violet-100/80 align-top">
                  <td className="px-1.5 py-1 font-medium text-zinc-900">{r.company_name}</td>
                  <td className="px-1.5 py-1 font-mono text-[10px] text-zinc-800">
                    {r.domain_sent || r.domain || '—'}
                  </td>
                  <td className="px-1.5 py-1 text-zinc-600" title={reqs.map((q) => q.filter_summary).join('\n')}>
                    {reqSummary}
                  </td>
                  <td
                    className={`px-1.5 py-1 font-semibold ${
                      blocked ? 'text-amber-800' : zero ? 'text-rose-700' : 'text-zinc-800'
                    }`}
                  >
                    {resultsLabel}
                    {!blocked && r.after_dedupe != null && r.after_dedupe !== r.prospeo_results
                      ? ` (${r.after_dedupe} únicos)`
                      : ''}
                  </td>
                  <td className="px-1.5 py-1 font-semibold text-emerald-800">{r.valid_results ?? 0}</td>
                  <td className="px-1.5 py-1 text-zinc-700">{r.discarded_count ?? 0}</td>
                  <td className="px-1.5 py-1 font-mono text-[9px] text-zinc-700">
                    {r.error_code || reqs.find((q) => q.error_code)?.error_code || '—'}
                  </td>
                  <td className="px-1.5 py-1 text-violet-900">
                    <div>{r.status_message || '—'}</div>
                    {(r.discard_reason || r.api_error) && (
                      <div className="mt-0.5 text-[9px] text-amber-900">
                        {r.api_error || r.discard_reason}
                      </div>
                    )}
                    {broadReq?.response_preview ? (
                      <div className="mt-1 font-mono text-[9px] text-zinc-600">
                        broad body: {broadReq.response_preview}
                      </div>
                    ) : null}
                    {Array.isArray(r.person_discards) && r.person_discards.length > 0 ? (
                      <ul className="mt-1 list-inside list-disc text-[9px] text-zinc-600">
                        {r.person_discards.slice(0, 5).map((d, j) => (
                          <li key={j}>
                            {d.person_name || '?'}: {d.reason}{' '}
                            <span className="text-zinc-400">({d.stage})</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </details>
  )
}

/** Debug: contactos descartados por validación Prospeo (directorio vs empresa objetivo). */
export function ProspeoContactDebugPanel({ rows = [] }) {
  const discarded = rows.filter((r) => r && r.ok === false)
  if (!discarded.length) return null
  return (
    <details className="mt-3 rounded-lg border border-amber-200 bg-amber-50/40 p-3">
      <summary className="cursor-pointer text-xs font-semibold text-amber-950">
        Debug Prospeo — {discarded.length} contacto(s) descartado(s)
      </summary>
      <div className="mt-2 max-h-64 overflow-auto">
        <table className="w-full min-w-[720px] text-left text-[10px]">
          <thead>
            <tr className="border-b border-amber-200 font-bold uppercase text-zinc-500">
              <th className="px-1.5 py-1">Empresa objetivo</th>
              <th className="px-1.5 py-1">Persona</th>
              <th className="px-1.5 py-1">Empresa detectada</th>
              <th className="px-1.5 py-1">Dominio objetivo</th>
              <th className="px-1.5 py-1">Dominio email</th>
              <th className="px-1.5 py-1">Motivo</th>
            </tr>
          </thead>
          <tbody>
            {discarded.slice(0, 60).map((r, i) => (
              <tr key={`${r.person_name}-${r.target_company}-${i}`} className="border-b border-amber-100/80">
                <td className="px-1.5 py-1 text-zinc-900">{r.company_target || r.target_company}</td>
                <td className="px-1.5 py-1">{r.person_name || '—'}</td>
                <td className="px-1.5 py-1 text-zinc-700">{r.detected_company || '—'}</td>
                <td className="px-1.5 py-1 text-zinc-600">{r.target_domain || '—'}</td>
                <td className="px-1.5 py-1 text-zinc-600">{r.email_domain ? `@${r.email_domain}` : '—'}</td>
                <td className="px-1.5 py-1 text-amber-900">{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
