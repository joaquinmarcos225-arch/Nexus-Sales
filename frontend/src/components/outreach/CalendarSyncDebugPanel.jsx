import { useMemo, useState } from 'react'

/**
 * Diagnóstico post “Sincronizar Calendar”: eventos, attendees, matches, pipeline.
 * `embedded`: usado dentro de la sección fija “Debug Calendar” (marco más liviano).
 */
export function CalendarSyncDebugPanel({ data, onClose, embedded = false }) {
  const [showRaw, setShowRaw] = useState(true)

  const rawJson = useMemo(() => {
    if (!data) {
      return ''
    }
    try {
      return JSON.stringify(data, null, 2)
    } catch {
      return String(data)
    }
  }, [data])

  const debugEvents = Array.isArray(data?.debug) ? data.debug : []
  const prospectIndex = Array.isArray(data?.prospect_email_index) ? data.prospect_email_index : []

  async function copyRaw() {
    if (!rawJson) {
      return
    }
    try {
      await navigator.clipboard.writeText(rawJson)
    } catch {
      /* ignore */
    }
  }

  if (!data) {
    return null
  }

  const shellClass = embedded
    ? 'mt-3 w-full rounded-lg border border-amber-400 bg-amber-50/95 shadow-inner'
    : 'mt-4 w-full rounded-xl border-2 border-amber-400 bg-amber-50/90 shadow-sm'

  return (
    <section
      className={shellClass}
      aria-label="Debug Calendar sync"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-300/80 bg-amber-100/80 px-4 py-2.5">
        <div>
          <h3 className="text-sm font-bold tracking-tight text-amber-950">
            {embedded ? 'Detalle del último sync' : 'Debug Calendar'}
          </h3>
          <p className="text-[11px] text-amber-900/90">
            Eventos, invitados (attendees), matches, skip_reason, reuniones creadas/actualizadas, pipeline
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded-md border border-amber-400 bg-white px-2.5 py-1 text-[11px] font-semibold text-amber-950 hover:bg-amber-50"
            onClick={() => setShowRaw((v) => !v)}
          >
            {showRaw ? 'Ocultar JSON' : 'Ver JSON completo'}
          </button>
          <button
            type="button"
            className="rounded-md border border-amber-400 bg-white px-2.5 py-1 text-[11px] font-semibold text-amber-950 hover:bg-amber-50"
            onClick={() => void copyRaw()}
          >
            Copiar JSON
          </button>
          {onClose ? (
            <button
              type="button"
              className="rounded-md px-2.5 py-1 text-[11px] font-medium text-amber-900 hover:bg-amber-200/60"
              onClick={onClose}
            >
              {embedded ? 'Ocultar detalle' : 'Cerrar'}
            </button>
          ) : null}
        </div>
      </div>

      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Eventos leídos" value={data.events_seen} />
        <Stat label="GET ok" value={data.events_enriched} />
        <Stat label="Prospectos email" value={data.prospects_with_email} />
        <Stat label="Match" value={data.matched} highlight={Number(data.matched) > 0} />
        <Stat label="Meetings creados" value={data.created} />
        <Stat label="Meetings actualizados" value={data.updated} />
        <Stat label="Pipeline ok" value={data.pipeline_updated} />
        <Stat label="Timeline" value={data.timelines_logged} />
      </div>

      {data.events_list_debug ? (
        <div className="mx-4 mb-3 space-y-3">
          <EventsListRequestDebug eld={data.events_list_debug} eventsSeen={data.events_seen} />
          {data.calendar_list_debug ? <CalendarListDebug cld={data.calendar_list_debug} /> : null}
        </div>
      ) : Number(data.events_seen) === 0 ? (
        <p className="mx-4 mb-3 rounded border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-900">
          <strong>events_seen = 0</strong> y no llegó <code className="rounded bg-rose-100 px-1">events_list_debug</code>.
          Actualizá el backend y volvé a sincronizar con <code className="rounded bg-rose-100 px-1">include_debug: true</code>.
        </p>
      ) : null}

      <p className="px-4 text-[11px] text-amber-900/90">
        Cuenta: <span className="font-mono font-medium">{data.calendar_account || '—'}</span>
        {' · '}
        Vendedor excluido: {(data.seller_emails || []).join(', ') || '—'}
        {data.time_window_start_utc ? (
          <>
            {' · '}
            Ventana: {String(data.time_window_start_utc).slice(0, 10)} →{' '}
            {String(data.time_window_end_utc || '').slice(0, 10)}
          </>
        ) : null}
      </p>

      {prospectIndex.length > 0 ? (
        <div className="mx-4 mb-3 rounded-lg border border-amber-300/70 bg-white/70 p-3">
          <p className="text-xs font-bold text-amber-950">Índice prospect.email</p>
          <ul className="mt-2 max-h-28 overflow-y-auto font-mono text-[10px] leading-relaxed text-zinc-800">
            {prospectIndex.map((p) => (
              <li key={p.prospect_id} className="border-b border-amber-100/80 py-0.5 last:border-0">
                #{p.prospect_id} {p.name}: <span className="text-zinc-500">{p.email_raw}</span> →{' '}
                <span className="font-semibold">{p.email_normalized}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mx-4 mb-3 text-xs font-medium text-rose-800">
          Sin prospectos con email en esta campaña — no hay nada que matchear.
        </p>
      )}

      <div className="mx-4 mb-3">
        <p className="text-xs font-bold text-amber-950">
          Eventos ({debugEvents.length} en debug)
        </p>
        {debugEvents.length === 0 ? (
          <p className="mt-2 text-xs text-amber-900">
            No hay eventos en el período o Google devolvió 0 items. Revisá arriba{' '}
            <strong>events.list</strong> (URL, timeMin/timeMax, JSON crudo). Si ahí{' '}
            <code className="rounded bg-amber-100 px-1">items</code> está vacío, el problema es API/ventana/calendario,
            no el matching.
          </p>
        ) : (
          <ul className="mt-2 max-h-[28rem] space-y-2 overflow-y-auto">
            {debugEvents.map((row, i) => (
              <EventTrace key={row.event_id || `ev-${i}`} row={row} />
            ))}
          </ul>
        )}
      </div>

      {Array.isArray(data.errors) && data.errors.length > 0 ? (
        <div className="mx-4 mb-3 rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-900">
          <p className="font-bold">Errores API</p>
          <ul className="mt-1 list-disc pl-4">
            {data.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {showRaw ? (
        <div className="border-t border-amber-300/80 p-4">
          <p className="mb-2 text-xs font-bold text-amber-950">Respuesta JSON cruda</p>
          <textarea
            readOnly
            className="h-64 w-full resize-y rounded-lg border border-amber-300 bg-zinc-950 p-3 font-mono text-[10px] leading-relaxed text-emerald-300"
            value={rawJson}
            spellCheck={false}
          />
        </div>
      ) : null}
    </section>
  )
}

function Stat({ label, value, highlight = false }) {
  return (
    <div
      className={`rounded-lg border px-2.5 py-2 ${
        highlight ? 'border-emerald-400 bg-emerald-50' : 'border-amber-200/80 bg-white/60'
      }`}
    >
      <p className="text-[10px] uppercase tracking-wide text-amber-800/80">{label}</p>
      <p className="text-lg font-bold tabular-nums text-amber-950">{value ?? 0}</p>
    </div>
  )
}

function EventsListRequestDebug({ eld, eventsSeen }) {
  const pages = Array.isArray(eld.pages) ? eld.pages : []
  const calendarsAll = Array.isArray(eld.calendars_all) ? eld.calendars_all : []
  const perCal = Array.isArray(eld.per_calendar_events) ? eld.per_calendar_events : []
  const queried = Array.isArray(eld.calendars_queried_for_events) ? eld.calendars_queried_for_events : []

  return (
    <div className="space-y-3">
      <div className="rounded-xl border-2 border-orange-600 bg-orange-50/95 p-3 shadow-sm">
        <p className="text-sm font-bold text-orange-950">Google Calendar · diagnóstico events.list + calendarList</p>
        {eld.sync_mode ? (
          <p className="mt-1 text-[11px] font-medium leading-snug text-orange-950">{eld.sync_mode}</p>
        ) : null}
        {eld.timezone_note ? (
          <p className="mt-1 text-[11px] leading-snug text-orange-900">{eld.timezone_note}</p>
        ) : null}
        {eld.time_window_anchor ? (
          <p className="mt-2 rounded border border-orange-300 bg-white/90 px-2 py-1 text-[11px] text-orange-950">
            Ventana temporal anclada a: <strong>{eld.time_window_anchor}</strong>
            {eld.time_window_anchor === 'client'
              ? ' (reloj del navegador; corrige servidor desfasado)'
              : ' (reloj del servidor)'}
            {eld.server_now_utc ? (
              <>
                <br />
                <span className="font-mono text-[10px]">server_now_utc: {eld.server_now_utc}</span>
              </>
            ) : null}
            {eld.window_now_utc ? (
              <>
                <br />
                <span className="font-mono text-[10px]">window_now_utc (usado): {eld.window_now_utc}</span>
              </>
            ) : null}
            {eld.client_now_utc_used_for_window ? (
              <>
                <br />
                <span className="font-mono text-[10px]">client_now: {eld.client_now_utc_used_for_window}</span>
              </>
            ) : null}
          </p>
        ) : null}
        {eld.calendar_list_error ? (
          <p className="mt-2 rounded border border-rose-400 bg-rose-50 px-2 py-1 text-xs font-semibold text-rose-900">
            calendarList: {eld.calendar_list_error}
          </p>
        ) : null}

        <dl className="mt-2 grid gap-1 font-mono text-[10px] text-orange-950">
          <div>
            <dt className="inline font-sans font-semibold">Eventos únicos tras merge: </dt>
            <dd className="inline">
              {eld.merged_unique_events_count ?? '—'} (events_seen API: {eventsSeen ?? '—'})
            </dd>
          </div>
          <div>
            <dt className="inline font-sans font-semibold">Calendarios en calendarList: </dt>
            <dd className="inline">{eld.calendar_list_total ?? calendarsAll.length}</dd>
          </div>
          <div>
            <dt className="inline font-sans font-semibold">IDs consultados en events.list: </dt>
            <dd className="inline break-all">{queried.join(', ') || '—'}</dd>
          </div>
          <div>
            <dt className="inline font-sans font-semibold">timeMin / timeMax (UTC Z, misma ventana todos): </dt>
            <dd className="inline">
              {eld.time_min_utc_rfc3339 ?? eld.time_window_repeated_utc?.timeMin} →{' '}
              {eld.time_max_utc_rfc3339 ?? eld.time_window_repeated_utc?.timeMax}
            </dd>
          </div>
        </dl>

        {calendarsAll.length > 0 ? (
          <div className="mt-3 rounded-lg border border-orange-400 bg-white/90 p-2">
            <p className="text-xs font-bold text-orange-950">
              Todos los calendarios (calendarList) — id · summary · primary · accessRole · timeZone
            </p>
            <div className="mt-2 max-h-56 overflow-auto">
              <table className="w-full border-collapse text-left text-[10px]">
                <thead>
                  <tr className="border-b border-orange-200 text-orange-800">
                    <th className="py-1 pr-2">summary</th>
                    <th className="py-1 pr-2">primary</th>
                    <th className="py-1 pr-2">accessRole</th>
                    <th className="py-1 pr-2">timeZone</th>
                    <th className="py-1">id</th>
                  </tr>
                </thead>
                <tbody>
                  {calendarsAll.map((c, i) => (
                    <tr key={c.id || i} className="border-b border-orange-100 align-top">
                      <td className="py-1 pr-2">{c.summary || '—'}</td>
                      <td className="py-1 pr-2">{c.primary ? 'yes' : ''}</td>
                      <td className="py-1 pr-2">{c.accessRole || '—'}</td>
                      <td className="py-1 pr-2">{c.timeZone || '—'}</td>
                      <td className="break-all font-mono text-[9px]">{c.id || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {perCal.length > 0 ? (
          <div className="mt-3 rounded-lg border border-orange-400 bg-white/90 p-2">
            <p className="text-xs font-bold text-orange-950">
              events.list por calendario (misma ventana UTC) — items_fetched_in_window
            </p>
            <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto font-mono text-[10px] text-zinc-900">
              {perCal.map((row, i) => (
                <li key={row.calendar_id || i} className="rounded border border-orange-100/80 bg-orange-50/50 px-2 py-1">
                  <span className="font-semibold text-orange-950">{row.items_fetched_in_window ?? 0} ev</span>
                  {' · '}
                  <span className="break-all">{row.calendar_id}</span>
                  {row.summary ? ` · ${row.summary}` : ''}
                  {row.accessRole ? ` · ${row.accessRole}` : ''}
                  {row.primary ? ' · [primary]' : ''}
                  {row.timeZone ? ` · TZ ${row.timeZone}` : ''}
                  {row.list_pages_count != null ? ` · páginas ${row.list_pages_count}` : ''}
                  {row.events_list_detail_included_full_raw ? ' · JSON crudo incluido abajo (este cal.)' : ''}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <p className="mt-3 text-[11px] font-semibold text-orange-950">
          Detalle events.list del calendario “{eld.calendar_id || 'primary'}” (referencia; suele ser primary)
        </p>
        {eld.calendar_id_note ? (
          <p className="text-[10px] text-orange-900">{eld.calendar_id_note}</p>
        ) : null}
        {Number(eventsSeen) === 0 && (eld.merged_unique_events_count ?? 0) === 0 ? (
          <p className="mt-2 rounded-md border border-orange-500 bg-white/90 px-2 py-1.5 text-xs font-semibold text-orange-950">
            Cero eventos en la ventana en <strong>todos</strong> los calendarios consultados. Revisá otra cuenta
            Google, otra ventana de fechas, o si el evento está en un calendario con solo freeBusyReader.
          </p>
        ) : null}

      <dl className="mt-2 grid gap-1 font-mono text-[10px] text-orange-950">
        <div>
          <dt className="inline font-sans font-semibold">Endpoint (este bloque): </dt>
          <dd className="inline break-all">{eld.list_endpoint}</dd>
        </div>
        {eld.timezone_for_query ? (
          <div>
            <dt className="inline font-sans font-semibold">Timezone query: </dt>
            <dd className="inline">{eld.timezone_for_query}</dd>
          </div>
        ) : null}
        <div>
          <dt className="inline font-sans font-semibold">timeMin (query, UTC Z): </dt>
          <dd className="inline">{eld.time_min_utc_rfc3339}</dd>
        </div>
        <div>
          <dt className="inline font-sans font-semibold">timeMax (query, UTC Z): </dt>
          <dd className="inline">{eld.time_max_utc_rfc3339}</dd>
        </div>
        <div>
          <dt className="inline font-sans font-semibold">Parámetros base: </dt>
          <dd className="inline break-all">{JSON.stringify(eld.base_query_params)}</dd>
        </div>
        <div>
          <dt className="inline font-sans font-semibold">Hora servidor Nexus (UTC): </dt>
          <dd className="inline">{eld.server_now_utc_iso}</dd>
        </div>
        <div>
          <dt className="inline font-sans font-semibold">Items (solo este calendar_id): </dt>
          <dd className="inline">{eld.total_items_collected}</dd>
        </div>
        <div>
          <dt className="inline font-sans font-semibold">Páginas: </dt>
          <dd className="inline">{eld.total_pages}</dd>
        </div>
      </dl>
      <p className="mt-2 text-[10px] text-orange-900">
        Autenticación: header <code className="rounded bg-white/80 px-1">Authorization: Bearer …</code> (el token no va
        en la URL).
      </p>
      {pages.map((p) => (
        <div key={p.page} className="mt-3 rounded-lg border border-orange-400 bg-white/90 p-2 text-[10px]">
          <p className="font-bold text-orange-950">
            Respuesta página {p.page} · HTTP {p.status_code}
            {p.calendar_id_queried ? ` · calendar ${p.calendar_id_queried}` : ''}
          </p>
          <p className="mt-1 break-all text-zinc-800">
            <span className="font-semibold text-orange-900">URL completa: </span>
            {p.request_url}
          </p>
          <p className="mt-1 text-zinc-800">
            <span className="font-semibold text-orange-900">params enviados: </span>
            {JSON.stringify(p.params_sent)}
          </p>
          <p className="mt-1 text-zinc-800">
            <span className="font-semibold text-orange-900">items en página: </span>
            {p.items_count_in_page}
            {p.next_page_token_present ? ' · hay nextPageToken' : ''}
          </p>
          {p.access_role_field != null ? (
            <p className="mt-1 text-zinc-700">
              accessRole: {String(p.access_role_field)} · summary: {String(p.calendar_summary_field ?? '—')} ·
              timeZone: {String(p.calendar_time_zone_field ?? '—')}
            </p>
          ) : null}
          {p.body_preview ? (
            <pre className="mt-2 max-h-36 overflow-auto rounded bg-zinc-900 p-2 font-mono text-[10px] text-emerald-200">
              {p.body_preview}
            </pre>
          ) : null}
          {p.raw_response_json ? (
            <>
              <p className="mt-2 font-semibold text-orange-950">JSON crudo de Google (esta página)</p>
              <textarea
                readOnly
                className="mt-1 h-44 w-full resize-y rounded border border-orange-300 bg-zinc-950 p-2 font-mono text-[10px] leading-relaxed text-emerald-300"
                value={p.raw_response_json}
                spellCheck={false}
              />
            </>
          ) : null}
        </div>
      ))}
      </div>
    </div>
  )
}

function CalendarListDebug({ cld }) {
  const pages = Array.isArray(cld.list_pages) ? cld.list_pages : []
  const allCal = Array.isArray(cld.calendars_all) ? cld.calendars_all : []

  return (
    <div className="rounded-xl border-2 border-violet-500 bg-violet-50/95 p-3 text-[11px] shadow-sm">
      <p className="text-sm font-bold text-violet-950">users/me/calendarList (respuesta paginada cruda)</p>
      <p className="mt-1 leading-snug text-violet-900">
        Mismo dato que la tabla naranja, acá ves cada página HTTP y el JSON que devolvió Google al listar calendarios.
      </p>
      <p className="mt-1 font-mono text-[10px] text-violet-800">
        URL: {cld.url || '—'} · total_calendars: {cld.total_calendars ?? allCal.length} · páginas:{' '}
        {cld.list_pages_count ?? pages.length}
      </p>
      {cld.error ? <p className="mt-2 text-rose-800">Error: {cld.error}</p> : null}

      {allCal.length > 0 ? (
        <p className="mt-2 text-[10px] text-violet-900">
          Primeros IDs:{' '}
          {allCal
            .slice(0, 6)
            .map((c) => c.id)
            .join(', ')}
          {allCal.length > 6 ? '…' : ''}
        </p>
      ) : null}

      {pages.map((pg, i) => (
        <div key={i} className="mt-3 rounded-lg border border-violet-300 bg-white/80 p-2 text-[10px]">
          <p className="font-bold text-violet-950">
            Página {i + 1} · HTTP {pg.status_code}
            {pg.items_in_page != null ? ` · items en página: ${pg.items_in_page}` : ''}
            {pg.next_page_token_present ? ' · nextPageToken' : ''}
          </p>
          <p className="mt-1 break-all font-mono text-zinc-700">{pg.request_url}</p>
          {pg.raw_response_json ? (
            <textarea
              readOnly
              className="mt-2 h-32 w-full resize-y rounded border border-violet-300 bg-zinc-950 p-2 font-mono text-[10px] text-emerald-300"
              value={pg.raw_response_json}
              spellCheck={false}
            />
          ) : null}
        </div>
      ))}
    </div>
  )
}

function EventTrace({ row }) {
  const guests = row.guest_attendees_normalized || []
  const comparisons = row.attendee_comparisons || []

  return (
    <li
      className={`rounded-lg border p-3 text-xs ${
        row.matched
          ? 'border-emerald-400 bg-emerald-50/90'
          : 'border-amber-200 bg-white/80'
      }`}
    >
      <p className="font-bold text-zinc-900">
        {row.matched ? '✓ MATCH' : '✗ sin match'}
        {row.summary ? ` · ${row.summary}` : ''}
      </p>
      <p className="mt-1 font-mono text-[10px] text-zinc-600">{row.event_id}</p>
      {row.source_calendar_id ? (
        <p className="text-[10px] text-violet-800">
          Calendar origen (events.list): <span className="font-mono">{row.source_calendar_id}</span>
        </p>
      ) : null}
      {row.start_utc ? <p className="text-[10px] text-zinc-600">Inicio: {row.start_utc}</p> : null}

      <dl className="mt-2 grid gap-1 text-[10px] text-zinc-800">
        <div>
          <dt className="inline font-semibold">GET: </dt>
          <dd className="inline">
            {row.get_ok ? 'ok' : 'falló'} (HTTP {row.get_http_status ?? '—'})
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">Organizer: </dt>
          <dd className="inline">
            {row.organizer_raw || '—'} → {row.organizer_normalized || '—'}
            {row.organizer_in_index ? ' [en índice]' : ''}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">Guest attendees: </dt>
          <dd className="inline">{guests.length ? guests.join(', ') : '—'}</dd>
        </div>
        {row.emails_in_description?.length ? (
          <div>
            <dt className="inline font-semibold">Emails en descripción: </dt>
            <dd className="inline">{row.emails_in_description.join(', ')}</dd>
          </div>
        ) : null}
      </dl>

      {comparisons.length > 0 ? (
        <table className="mt-2 w-full border-collapse text-[10px]">
          <thead>
            <tr className="border-b border-zinc-200 text-left text-zinc-500">
              <th className="py-1 pr-2">Attendee</th>
              <th className="py-1 pr-2">Norm</th>
              <th className="py-1">Resultado</th>
            </tr>
          </thead>
          <tbody>
            {comparisons.map((c, j) => (
              <tr key={j} className="border-b border-zinc-100">
                <td className="py-1 pr-2 font-mono">{c.email_raw || '—'}</td>
                <td className="py-1 pr-2 font-mono">{c.email_normalized}</td>
                <td className="py-1">
                  {c.is_seller
                    ? 'vendedor (ignorado)'
                    : c.match
                      ? `MATCH #${c.matched_prospect_id}`
                      : 'no en índice'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {row.matched ? (
        <p className="mt-2 font-medium text-emerald-900">
          Meeting: {row.meeting_created ? 'CREADO' : row.meeting_updated ? 'actualizado' : '—'} · Pipeline:{' '}
          {row.pipeline_updated ? 'SÍ' : `NO (${row.pipeline_skip_reason || '—'})`}
          {row.match_via ? ` · vía ${row.match_via}` : ''}
          {row.matched_email_normalized ? ` · ${row.matched_email_normalized}` : ''}
        </p>
      ) : null}

      {row.skip_reason ? (
        <p className="mt-2 font-semibold text-amber-900">skip_reason: {row.skip_reason}</p>
      ) : null}
      {row.error ? <p className="mt-1 text-rose-800">error: {row.error}</p> : null}
    </li>
  )
}
