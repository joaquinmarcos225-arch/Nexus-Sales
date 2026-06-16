import { Link } from 'react-router-dom'
import { useMemo } from 'react'
import { useCompany } from '../../context/CompanyContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { PageHeader } from '../../layout/PageHeader'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { useFlattenedProspects } from '../../hooks/useFlattenedProspects.js'
import {
  parseSequenceFired,
  sequenceCalendarDayIndex,
} from '../../utils/sequenceUi.js'
function Card({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">{label}</p>
      <p className="mt-2 text-xl font-semibold text-nx-ink">{value}</p>
      {hint ? <p className="mt-1 text-xs text-nx-muted">{hint}</p> : null}
    </div>
  )
}

function pct(x) {
  if (x == null || Number.isNaN(x)) {
    return '0%'
  }
  return `${(Number(x) * 100).toFixed(1)}%`
}

function fmtDate(iso) {
  if (!iso) {
    return '—'
  }
  try {
    return new Date(iso).toLocaleString('es-AR', {
      dateStyle: 'medium',
      timeStyle: 'short',
    })
  } catch {
    return '—'
  }
}

function Panel({ title, children }) {
  return (
    <div className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-wide text-nx-muted">{title}</p>
      <div className="mt-3">{children}</div>
    </div>
  )
}

export default function DashboardOverview() {
  const { company, companyId } = useCompany()
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const { rows: pulseRows, loading: loadingPulse } = useFlattenedProspects(companyId)
  const t = data?.totals
  const intel = data?.intelligence
  const commercial = data?.commercial

  const nexusPulse = useMemo(() => {
    const rows = pulseRows || []
    let linkedinListos = 0
    let descanso = 0
    let reactivacionesPend = 0
    let esperandoRespuesta = 0
    for (const p of rows) {
      if (
        (p.linkedin_url || '').trim() &&
        (p.linkedin_assisted_draft || '').trim() &&
        !p.linkedin_sdr_marked_sent_at
      ) {
        linkedinListos += 1
      }
      if (String(p.sequence_group || '').toLowerCase() === 'descanso') {
        descanso += 1
      }
      const st = String(p.status || '').toLowerCase()
      if (st === 'contacted' && !(p.last_inbound_at || '').trim()) {
        esperandoRespuesta += 1
      }
      if (p.sequence_started_at) {
        const day = sequenceCalendarDayIndex(p.sequence_started_at)
        const fired = parseSequenceFired(p.sequence_fired_milestones)
        if (
          day >= 42 &&
          fired.includes(21) &&
          !fired.includes(42) &&
          !p.sequence_paused &&
          String(p.sequence_group || '').toLowerCase() !== 'encajonado'
        ) {
          reactivacionesPend += 1
        }
      }
    }
    return {
      linkedinListos,
      descanso,
      reactivacionesPend,
      esperandoRespuesta,
    }
  }, [pulseRows])

  const campaignActivityFeed = useMemo(() => {
    const raw = data?.campaigns_summary ?? data?.campaigns ?? []
    const rows = Array.isArray(raw) ? raw : []
    return [...rows]
      .filter((c) => c.last_activity_at)
      .sort((a, b) => new Date(b.last_activity_at).getTime() - new Date(a.last_activity_at).getTime())
      .slice(0, 14)
  }, [data])

  const recommended = useMemo(() => {
    const raw = data?.recommended_actions
    return Array.isArray(raw) ? raw.slice(0, 12) : []
  }, [data])

  return (
    <>
      <PageHeader
        title="Resumen general"
        description={
          company
            ? `${company.name} · Métricas operativas y pipeline.`
            : 'Métricas de la empresa seleccionada.'
        }
      />
      <AlertBanner message={error} onDismiss={() => void refresh()} />

      {companyId ? (
        <div className="mt-6 rounded-xl border border-rose-100/90 bg-gradient-to-br from-zinc-50/90 via-white to-rose-50/20 p-4 shadow-sm ring-1 ring-zinc-900/5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-nx-ink">Nexus en vivo</h2>
              <p className="mt-0.5 text-xs text-nx-muted">
                Pulso operativo con datos ya cargados en la empresa (sin APIs nuevas).
                {loadingPulse ? ' Actualizando prospectos…' : ''}
              </p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <Card
              label="Preparación en cola"
              value={String(intel?.pending_tasks_total ?? 0)}
              hint="Tareas outreach pendientes (Nexus + SDR)."
            />
            <Card
              label="Follow-ups activos"
              value={String(data?.pending_followups ?? intel?.pending_scheduled_followups ?? 0)}
              hint="Seguimientos programados pendientes."
            />
            <Card
              label="LinkedIn listos para enviar"
              value={String(nexusPulse.linkedinListos)}
              hint="Con URL, borrador y sin marcar enviado."
            />
            <Card
              label="Leads esperando respuesta"
              value={String(nexusPulse.esperandoRespuesta)}
              hint="Estado contactado sin inbound registrado."
            />
            <Card label="Leads en descanso" value={String(nexusPulse.descanso)} hint="Grupo descanso (día 22–41)." />
            <Card
              label="Reactivaciones pendientes"
              value={String(nexusPulse.reactivacionesPend)}
              hint="Día ≥ 42, hito 21 hecho, 42 aún no."
            />
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <Panel title="Últimas acciones de Nexus (por campaña)">
              {campaignActivityFeed.length ? (
                <ul className="max-h-64 space-y-2 overflow-y-auto text-sm">
                  {campaignActivityFeed.map((c) => (
                    <li
                      key={c.campaign_id ?? c.name}
                      className="flex flex-wrap items-baseline justify-between gap-2 border-b border-nx-border/40 pb-2 last:border-0"
                    >
                      <span className="font-medium text-nx-ink">{c.name}</span>
                      <span className="text-xs text-nx-muted">{fmtDate(c.last_activity_at)}</span>
                      <span className="w-full text-[11px] text-nx-muted">
                        {c.messages_sent != null ? `${c.messages_sent} mensajes · ` : ''}
                        {c.prospects_contacted != null ? `${c.prospects_contacted} contactados` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-nx-muted">Sin actividad reciente indexada.</p>
              )}
            </Panel>
            <Panel title="Prioridades sugeridas (cola)">
              {recommended.length ? (
                <ul className="max-h-64 space-y-2 overflow-y-auto text-sm">
                  {recommended.map((a) => (
                    <li key={a.id} className="border-b border-nx-border/40 pb-2 last:border-0">
                      <p className="font-medium text-nx-ink">{a.headline || a.title}</p>
                      <p className="text-[11px] text-nx-muted">
                        {a.campaign_name}
                        {a.prospect_name ? ` · ${a.prospect_name}` : ''}
                      </p>
                      {a.suggested_action ? (
                        <p className="mt-1 text-xs text-nx-ink/90">{a.suggested_action}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-nx-muted">Sin tareas priorizadas en este momento.</p>
              )}
            </Panel>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="rounded-lg border border-nx-border bg-nx-card px-4 py-6 text-center text-sm text-nx-muted">
          Cargando analítica…
        </div>
      ) : null}

      {t ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            <Card label="Campañas activas" value={String(t.campaigns_active)} hint="En curso o listas." />
            <Card label="Campañas pausadas" value={String(t.campaigns_paused)} />
            <Card
              label="Otras campañas"
              value={String(t.campaigns_other)}
              hint="Borrador, completadas u otros estados."
            />
            <Card label="Prospectos importados" value={String(t.prospects_imported)} />
            <Card label="Prospectos activos (pipeline)" value={String(t.prospects_active)} />
            <Card label="Contactados" value={String(t.prospects_contacted)} />
            <Card label="Respondieron" value={String(t.prospects_responded)} />
            <Card label="Interesados" value={String(t.prospects_interested)} />
            <Card label="Reuniones generadas" value={String(t.meetings_booked)} />
            <Card label="Tasa de respuesta" value={pct(t.response_rate)} />
            <Card label="Tasa de interés (sobre respuestas)" value={pct(t.interest_rate)} />
            <Card label="Mensajes enviados" value={String(t.messages_sent)} hint="Outbound registrados." />
            <Card label="Última actividad" value={fmtDate(t.last_activity_at)} />
          </div>

          {commercial ? (
            <>
              <h2 className="mt-10 text-base font-semibold text-nx-ink">Pipeline y reuniones</h2>
              <p className="mt-1 text-sm text-nx-muted">
                Módulo Meeting + etapas comerciales. Integración de calendario externo en una fase posterior.
              </p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
                <Card label="Reuniones pendientes" value={String(commercial.meetings_pending)} />
                <Card label="Reuniones confirmadas" value={String(commercial.meetings_confirmed)} />
                <Card label="Reuniones completadas" value={String(commercial.meetings_completed)} />
                <Card
                  label="Tasa completitud reuniones"
                  value={pct(commercial.meeting_completion_rate)}
                  hint="Completadas sobre activas (pend./conf./compl.)."
                />
                <Card label="Total reuniones (registro)" value={String(commercial.meetings_total)} />
                <Card
                  label="Pipeline abierto"
                  value={String(commercial.pipeline_open_count)}
                  hint="Prospectos fuera de ganado/perdido."
                />
              </div>
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <Panel title="Conversiones por etapa (conteo)">
                  {commercial.pipeline_by_stage &&
                  Object.keys(commercial.pipeline_by_stage).length > 0 ? (
                    <div className="overflow-x-auto max-h-56 overflow-y-auto">
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr className="border-b border-nx-border text-[11px] uppercase text-nx-muted">
                            <th className="py-2 font-semibold">Etapa</th>
                            <th className="py-2 text-right font-semibold">Prospectos</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(commercial.pipeline_by_stage)
                            .sort((a, b) => b[1] - a[1])
                            .map(([stage, count]) => (
                              <tr key={stage} className="border-b border-nx-border/50">
                                <td className="py-2 font-mono text-xs text-nx-ink">{stage}</td>
                                <td className="py-2 text-right tabular-nums">{count}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-nx-muted">Sin etapas asignadas aún.</p>
                  )}
                </Panel>
                <Panel title="Campañas con más reuniones registradas">
                  {(commercial.top_campaigns_by_meetings || []).length ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr className="border-b border-nx-border text-[11px] uppercase text-nx-muted">
                            <th className="py-2 pr-2 font-semibold">Campaña</th>
                            <th className="py-2 text-right font-semibold">Reuniones</th>
                          </tr>
                        </thead>
                        <tbody>
                          {commercial.top_campaigns_by_meetings.map((row) => (
                            <tr key={row.campaign_id} className="border-b border-nx-border/50">
                              <td className="max-w-[12rem] truncate py-2 pr-2 text-nx-ink" title={row.campaign_name}>
                                {row.campaign_name}
                              </td>
                              <td className="py-2 text-right tabular-nums">{row.meetings}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-nx-muted">Creá reuniones desde el detalle de prospecto.</p>
                  )}
                </Panel>
              </div>
            </>
          ) : null}

          {intel ? (
            <>
              <h2 className="mt-10 text-base font-semibold text-nx-ink">Inteligencia de outreach</h2>
              <p className="mt-1 text-sm text-nx-muted">
                Follow-ups, objeciones, interés y momentum de reunión (capa lista para cron y canales
                reales).
              </p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <Card
                  label="Prospectos calientes"
                  value={String(intel.hot_prospects)}
                  hint="Alta probabilidad / pipeline interesado."
                />
                <Card
                  label="Follow-ups programados pendientes"
                  value={String(intel.pending_scheduled_followups)}
                />
                <Card label="Tareas outreach pendientes" value={String(intel.pending_tasks_total)} />
                <Card
                  label="Sugerencias de reunión enviadas (IA)"
                  value={String(intel.ia_meeting_nudges)}
                />
                <Card
                  label="Momentum reunión (IA)"
                  value={String(intel.suggested_meeting_momentum)}
                  hint="Alta señal + 2+ respuestas del prospecto."
                />
              </div>

              <div className="mt-6 grid gap-4 lg:grid-cols-2">
                <Panel title="Objeciones frecuentes">
                  {(intel.objections_top || []).length ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr className="border-b border-nx-border text-[11px] uppercase text-nx-muted">
                            <th className="py-2 pr-2 font-semibold">Tipo</th>
                            <th className="py-2 text-right font-semibold">Veces</th>
                          </tr>
                        </thead>
                        <tbody>
                          {intel.objections_top.map((row, i) => (
                            <tr key={`${row.objection_type}-${i}`} className="border-b border-nx-border/50">
                              <td className="py-2 pr-2 font-mono text-xs text-nx-ink">
                                {row.objection_type}
                              </td>
                              <td className="py-2 text-right tabular-nums text-nx-ink">{row.count}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-nx-muted">Sin objeciones etiquetadas aún.</p>
                  )}
                </Panel>

                <Panel title="Interés por campaña (% prospectos)">
                  {(intel.interest_by_campaign || []).length ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr className="border-b border-nx-border text-[11px] uppercase text-nx-muted">
                            <th className="py-2 pr-2 font-semibold">Campaña</th>
                            <th className="py-2 text-right font-semibold">Alto</th>
                            <th className="py-2 text-right font-semibold">Medio</th>
                            <th className="py-2 text-right font-semibold">Bajo</th>
                          </tr>
                        </thead>
                        <tbody>
                          {intel.interest_by_campaign.slice(0, 12).map((row) => (
                            <tr
                              key={row.campaign_id}
                              className="border-b border-nx-border/50"
                            >
                              <td className="max-w-[10rem] truncate py-2 pr-2 text-nx-ink" title={row.campaign_name}>
                                {row.campaign_name}
                              </td>
                              <td className="py-2 text-right tabular-nums">{row.high_pct}%</td>
                              <td className="py-2 text-right tabular-nums">{row.medium_pct}%</td>
                              <td className="py-2 text-right tabular-nums">{row.low_pct}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-nx-muted">Sin campañas con prospectos para este cálculo.</p>
                  )}
                </Panel>
              </div>

              <div className="mt-4">
                <Panel title="Tasa de respuesta por industria (contactados → respondieron)">
                  {(intel.industry_response_rates || []).length ? (
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[520px] text-left text-sm">
                        <thead>
                          <tr className="border-b border-nx-border text-[11px] uppercase text-nx-muted">
                            <th className="py-2 pr-2 font-semibold">Industria</th>
                            <th className="py-2 text-right font-semibold">Contactados</th>
                            <th className="py-2 text-right font-semibold">Respondieron</th>
                            <th className="py-2 text-right font-semibold">Tasa</th>
                          </tr>
                        </thead>
                        <tbody>
                          {intel.industry_response_rates.map((row) => (
                            <tr key={row.industry} className="border-b border-nx-border/50">
                              <td className="max-w-[14rem] truncate py-2 pr-2 text-nx-ink" title={row.industry}>
                                {row.industry}
                              </td>
                              <td className="py-2 text-right tabular-nums">{row.contacted}</td>
                              <td className="py-2 text-right tabular-nums">{row.responded}</td>
                              <td className="py-2 text-right tabular-nums">{pct(row.rate)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-nx-muted">Sin datos de industria con contacto.</p>
                  )}
                </Panel>
              </div>

            </>
          ) : null}

          <div className="mt-10 rounded-xl border border-dashed border-nx-border bg-nx-card-muted/40 p-4 text-sm text-nx-muted">
            <p className="font-medium text-nx-ink">Secciones detalladas</p>
            <p className="mt-1">
              Usá el menú lateral bajo <span className="font-semibold">Dashboard</span> para ver tablas y
              gráficos por campaña, prospectos, outreach y equipo.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link
                className="rounded-lg bg-nx-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-nx-brand-hover"
                to="/dashboard/campanas"
              >
                Campañas
              </Link>
              <Link
                className="rounded-lg border border-nx-border bg-white px-3 py-1.5 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted"
                to="/dashboard/prospectos"
              >
                Prospectos
              </Link>
              <Link
                className="rounded-lg border border-nx-border bg-white px-3 py-1.5 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted"
                to="/dashboard/equipo"
              >
                Equipo
              </Link>
            </div>
          </div>
        </>
      ) : null}

      {!loading && !t && !error ? (
        <p className="text-sm text-nx-muted">Sin datos de analítica para esta empresa.</p>
      ) : null}
    </>
  )
}
