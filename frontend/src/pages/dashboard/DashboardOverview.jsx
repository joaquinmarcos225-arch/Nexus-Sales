import { Link } from 'react-router-dom'
import { useMemo } from 'react'
import { useCompany } from '../../context/CompanyContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { PageHeader } from '../../layout/PageHeader'
import { useDashboardAnalytics } from '../../context/DashboardAnalyticsContext.jsx'
import { useFlattenedProspects } from '../../hooks/useFlattenedProspects.js'
import {
  parseSequenceFired,
  PLAYBOOK_LAST_TOUCH_DAY,
  REACTIVATION_DAY,
  sequenceCalendarDayIndex,
} from '../../utils/sequenceUi.js'
import { useAuth } from '../../context/AuthContext.jsx'
import { WorkspaceGoLiveChecklist } from '../../components/dashboard/WorkspaceGoLiveChecklist.jsx'
import { SdrConsolePillars } from '../../components/dashboard/SdrConsolePillars.jsx'
import { ConsoleActionPanel } from '../../components/dashboard/ConsoleActionPanel.jsx'
import { Panel, StatCard } from '../../components/ui/Card.jsx'
import { PageSection } from '../../components/ui/PageSection.jsx'
import { userDisplayFirstName } from '../../utils/userDisplayName.js'
import { useLinkedInPending } from '../../hooks/useLinkedInPending.js'
import { useWhatsAppPending } from '../../hooks/useWhatsAppPending.js'
import { useMeetingsPending } from '../../hooks/useMeetingsPending.js'
import { useResponderPending } from '../../hooks/useResponderPending.js'

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

export default function DashboardOverview() {
  const { company, companyId } = useCompany()
  const { user } = useAuth()
  const { data, loading, error, refresh } = useDashboardAnalytics()
  const { rows: pulseRows, loading: loadingPulse } = useFlattenedProspects(companyId)
  const { count: linkedInPending, href: linkedInHref } = useLinkedInPending(companyId)
  const { count: whatsAppPending, href: whatsAppHref } = useWhatsAppPending(companyId)
  const { count: meetingsPending, href: meetingsHref } = useMeetingsPending(companyId)
  const { count: responderPending, href: responderHref } = useResponderPending(companyId)
  const t = data?.totals
  const intel = data?.intelligence
  const commercial = data?.commercial
  const firstName = userDisplayFirstName(user)

  const nexusPulse = useMemo(() => {
    const rows = pulseRows || []
    let linkedinConectar = 0
    let descanso = 0
    let reactivacionesPend = 0
    let esperandoRespuesta = 0
    for (const p of rows) {
      const hasLinkedIn = (p.linkedin_url || '').trim()
      if (hasLinkedIn && String(p.linkedin_connection_status || '').toLowerCase() === 'invite_pending') {
        linkedinConectar += 1
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
          day >= REACTIVATION_DAY &&
          fired.includes(PLAYBOOK_LAST_TOUCH_DAY) &&
          !fired.includes(REACTIVATION_DAY) &&
          !p.sequence_paused &&
          String(p.sequence_group || '').toLowerCase() !== 'encajonado'
        ) {
          reactivacionesPend += 1
        }
      }
    }
    return {
      linkedinConectar,
      descanso,
      reactivacionesPend,
      esperandoRespuesta,
    }
  }, [pulseRows])

  return (
    <>
      <PageHeader
        kicker="Consola"
        title={firstName ? `Hola, ${firstName}. Esto es lo que pasa hoy` : 'Resumen del día'}
        actions={null}
      />
      <AlertBanner message={error} onDismiss={() => void refresh()} />

      {companyId ? (
        <div className="mt-6 space-y-6">
          <WorkspaceGoLiveChecklist />
          <SdrConsolePillars />

          <PageSection
            title="Tu día"
            description="Indicadores accionables para la jornada."
            collapsible={false}
          >
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <div className="grid gap-3 sm:grid-cols-2">
                  <StatCard
                    label="LinkedIn por enviar"
                    value={String(linkedInPending)}
                    hint="Cola LinkedIn asistida (todas las campañas)."
                  />
                  <StatCard
                    label="WhatsApp por enviar"
                    value={String(whatsAppPending)}
                    hint="Cola real de WhatsApp (asistido)."
                  />
                  <StatCard
                    label="Reuniones programadas"
                    value={String(
                      (commercial?.meetings_pending ?? 0) + (commercial?.meetings_confirmed ?? 0),
                    )}
                    hint="Reuniones pendientes o confirmadas en agenda."
                  />
                  <StatCard
                    label="Follow-ups pendientes"
                    value={String(data?.pending_followups ?? intel?.pending_scheduled_followups ?? 0)}
                    hint="Seguimientos programados."
                  />
                  <StatCard
                    label="Preparación en cola"
                    value={String(intel?.pending_tasks_total ?? 0)}
                    hint="Tareas outreach pendientes."
                  />
                </div>
                {loadingPulse ? (
                  <p className="mt-3 text-xs text-nx-muted">Actualizando prospectos en vivo…</p>
                ) : null}
              </div>

              <ConsoleActionPanel
                todos={[
                  {
                    id: 'responder',
                    label: 'Responder',
                    count: responderPending,
                    to: responderPending > 0 ? responderHref : '/campanas',
                    tone: 'alert',
                  },
                  {
                    id: 'linkedin',
                    label: 'LinkedIn por enviar',
                    count: linkedInPending,
                    to: linkedInHref,
                    tone: 'linkedin',
                  },
                  {
                    id: 'whatsapp',
                    label: 'WhatsApp por enviar',
                    count: whatsAppPending,
                    to: whatsAppHref,
                    tone: 'whatsapp',
                  },
                  {
                    id: 'meetings',
                    label: 'Reuniones agendadas',
                    count: meetingsPending,
                    to: meetingsHref,
                    tone: 'meeting',
                  },
                ]}
              />
            </div>
          </PageSection>
        </div>
      ) : null}

      {loading ? (
        <div className="rounded-lg border border-nx-border bg-nx-card px-4 py-6 text-center text-sm text-nx-muted">
          Cargando analítica…
        </div>
      ) : null}

      {t ? (
        <div className="mt-6 space-y-4">
          <PageSection
            title="Pulso operativo"
            description="Señales de secuencia y respuesta (menos urgentes que «Tu día»)."
            defaultOpen={false}
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <StatCard
                label="Esperando respuesta"
                value={String(nexusPulse.esperandoRespuesta)}
                hint="Contactados sin inbound."
              />
              <StatCard label="En descanso" value={String(nexusPulse.descanso)} hint="Grupo descanso (día 22–41)." />
              <StatCard
                label="Reactivaciones pendientes"
                value={String(nexusPulse.reactivacionesPend)}
                hint={`Día ≥ ${REACTIVATION_DAY}, hito ${PLAYBOOK_LAST_TOUCH_DAY} sin ${REACTIVATION_DAY}.`}
              />
            </div>
          </PageSection>

          <PageSection
            title="Métricas generales"
            description="Campañas, prospectos y tasas de la empresa."
            defaultOpen={false}
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              <StatCard label="Campañas activas" value={String(t.campaigns_active)} hint="En curso o listas." />
              <StatCard label="Campañas pausadas" value={String(t.campaigns_paused)} />
              <StatCard
                label="Otras campañas"
                value={String(t.campaigns_other)}
                hint="Borrador, completadas u otros estados."
              />
              <StatCard label="Prospectos importados" value={String(t.prospects_imported)} />
              <StatCard label="Prospectos activos" value={String(t.prospects_active)} />
              <StatCard label="Contactados" value={String(t.prospects_contacted)} />
              <StatCard label="Respondieron" value={String(t.prospects_responded)} />
              <StatCard label="Interesados" value={String(t.prospects_interested)} />
              <StatCard label="Reuniones generadas" value={String(t.meetings_booked)} />
              <StatCard label="Tasa de respuesta" value={pct(t.response_rate)} />
              <StatCard label="Tasa de interés" value={pct(t.interest_rate)} />
              <StatCard label="Mensajes enviados" value={String(t.messages_sent)} />
              <StatCard label="Última actividad" value={fmtDate(t.last_activity_at)} />
            </div>
          </PageSection>

          {commercial ? (
            <PageSection
              title="Pipeline y reuniones"
              description="Etapas comerciales y reuniones por estado."
              defaultOpen={false}
            >
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
                <StatCard label="Reuniones pendientes" value={String(commercial.meetings_pending)} />
                <StatCard label="Reuniones confirmadas" value={String(commercial.meetings_confirmed)} />
                <StatCard label="Reuniones completadas" value={String(commercial.meetings_completed)} />
                <StatCard
                  label="Tasa completitud"
                  value={pct(commercial.meeting_completion_rate)}
                  hint="Completadas sobre activas."
                />
                <StatCard label="Total reuniones" value={String(commercial.meetings_total)} />
                <StatCard
                  label="Pipeline abierto"
                  value={String(commercial.pipeline_open_count)}
                  hint="Fuera de ganado/perdido."
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
            </PageSection>
          ) : null}

          {intel ? (
            <PageSection
              title="Inteligencia de outreach"
              description="Objeciones, interés por campaña e industria."
              defaultOpen={false}
            >
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <StatCard
                  label="Prospectos calientes"
                  value={String(intel.hot_prospects)}
                  hint="Alta probabilidad / pipeline interesado."
                />
                <StatCard
                  label="Follow-ups programados pendientes"
                  value={String(intel.pending_scheduled_followups)}
                />
                <StatCard label="Tareas outreach pendientes" value={String(intel.pending_tasks_total)} />
                <StatCard
                  label="Sugerencias de reunión enviadas (IA)"
                  value={String(intel.ia_meeting_nudges)}
                />
                <StatCard
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

            </PageSection>
          ) : null}

          <div className="nx-card-muted flex flex-wrap items-center gap-2 rounded-xl px-4 py-3 text-sm">
            <span className="text-nx-muted">Más detalle:</span>
            <Link to="/dashboard/outreach" className="nx-btn nx-btn-ghost text-xs">
              Outreach
            </Link>
            <Link to="/dashboard/reuniones" className="nx-btn nx-btn-ghost text-xs">
              Reuniones
            </Link>
            <Link to="/campanas" className="nx-btn nx-btn-ghost text-xs">
              Campañas
            </Link>
          </div>
        </div>
      ) : null}

      {!loading && !t && !error ? (
        <p className="text-sm text-nx-muted">Sin datos de analítica para esta empresa.</p>
      ) : null}
    </>
  )
}
