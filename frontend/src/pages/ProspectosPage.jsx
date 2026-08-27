import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { Modal } from '../components/Modal.jsx'
import { ActiveSequencesPanel } from '../components/prospects/ActiveSequencesPanel.jsx'
import { ProspectConversationModal } from '../components/prospects/ProspectConversationModal.jsx'
import { ProspectOutreachPanel } from '../components/prospects/ProspectOutreachPanel.jsx'
import { PageHeader } from '../layout/PageHeader'
import { PageSection } from '../components/ui/PageSection.jsx'
import { ROLES, normalizeRole, isCompanyAdmin } from '../data/navigation.js'
import {
  claimProspect,
  fetchActiveSequences,
  fetchProspectsOwnership,
  fetchTestingResetAvailability,
  fetchUsers,
  reassignProspect,
  releaseProspect,
  resetTestingData,
  startProspectSequence,
} from '../utils/api.js'
import {
  CLAIMABLE_OWNERSHIP_STATUSES,
  fmtDateTime,
  formatLastSequence,
  ownerDisplayLabel,
  ownershipStatusBadgeClass,
  ownershipStatusLabel,
} from '../utils/ownershipUi.js'
import {
  COMMERCIAL_FILTER_OPTIONS,
  COMMERCIAL_PIPELINE_CHIPS,
  commercialStateBadgeClass,
  commercialStateLabel,
  pipelineChipToneClass,
  testingBadgeClass,
} from '../utils/commercialUi.js'

const OWNERSHIP_FILTER_OPTIONS = [
  { value: '', label: 'Todos (ownership)' },
  { value: 'libre', label: 'Libre' },
  { value: 'tomado', label: 'Tomado' },
  { value: 'en_secuencia', label: 'En secuencia' },
  { value: 'secuencia_finalizada', label: 'Finalizado' },
  { value: 'liberado', label: 'Liberado' },
]

function pageDescription(role) {
  const r = normalizeRole(role)
  if (r === ROLES.sdr) {
    return 'Tomá prospectos libres, generá secuencias e iniciá outreach desde esta bandeja.'
  }
  if (r === ROLES.manager) {
    return 'Supervisá prospectos de la empresa y tomá libres para tu equipo.'
  }
  return 'Vista completa de prospectos, ownership y reglas de asignación.'
}

function sequenceStatusSubline(prospect) {
  if (prospect.ownership_status !== 'en_secuencia') {
    return null
  }
  if (prospect.sequence_current_day_label) {
    return `Día actual: ${prospect.sequence_current_day_label}`
  }
  if (prospect.next_touch_label) {
    const dayPart = prospect.next_touch_label.split(' ·')[0]
    return `Próximo toque: ${dayPart}`
  }
  return null
}

export default function ProspectosPage() {
  const { user: authUser } = useAuth()
  const { companyId, loading: ctxLoading } = useCompany()
  const [workspace, setWorkspace] = useState(null)
  const [sdrUsers, setSdrUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [commercialFilter, setCommercialFilter] = useState('')
  const [showSimulations, setShowSimulations] = useState(false)
  const [search, setSearch] = useState('')

  const [reassignOpen, setReassignOpen] = useState(false)
  const [reassignTarget, setReassignTarget] = useState(null)
  const [reassignUserId, setReassignUserId] = useState('')

  const [rulesOpen, setRulesOpen] = useState(false)
  const [outreachOpen, setOutreachOpen] = useState(false)
  const [outreachProspect, setOutreachProspect] = useState(null)
  const [outreachMode, setOutreachMode] = useState('view')
  const [conversationOpen, setConversationOpen] = useState(false)
  const [conversationProspect, setConversationProspect] = useState(null)
  const [activeSequences, setActiveSequences] = useState([])
  const [activeLoading, setActiveLoading] = useState(false)
  const [testingResetAvailable, setTestingResetAvailable] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const [resetResult, setResetResult] = useState(null)

  const loadData = useCallback(async () => {
    if (!companyId) {
      return
    }
    setLoading(true)
    setActiveLoading(true)
    try {
      setError(null)
      const [ws, users, active] = await Promise.all([
        fetchProspectsOwnership(companyId, { includeTesting: showSimulations }),
        fetchUsers(companyId),
        fetchActiveSequences(companyId).catch(() => ({ sequences: [] })),
      ])
      setWorkspace(ws)
      setActiveSequences(active?.sequences || [])
      setSdrUsers(
        (Array.isArray(users) ? users : []).filter(
          (u) => normalizeRole(u.role) === ROLES.sdr && u.is_active !== false,
        ),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setWorkspace(null)
      setSdrUsers([])
      setActiveSequences([])
    } finally {
      setLoading(false)
      setActiveLoading(false)
    }
  }, [companyId, showSimulations])

  useEffect(() => {
    void loadData()
  }, [loadData])

  useEffect(() => {
    if (!companyId) {
      setTestingResetAvailable(false)
      return
    }
    if (!isCompanyAdmin(authUser)) {
      setTestingResetAvailable(false)
      return
    }
    void fetchTestingResetAvailability(companyId)
      .then((res) => setTestingResetAvailable(Boolean(res?.enabled)))
      .catch(() => setTestingResetAvailable(false))
  }, [companyId, authUser?.role])

  const caps = workspace?.capabilities ?? {}
  const prospects = workspace?.prospects ?? []
  const viewerRole = workspace?.viewer_role ?? authUser?.role

  const commercialSummary = workspace?.commercial_summary

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return prospects.filter((p) => {
      if (statusFilter && p.ownership_status !== statusFilter) {
        return false
      }
      if (commercialFilter && p.commercial_state !== commercialFilter) {
        return false
      }
      if (!q) {
        return true
      }
      const hay = [
        p.company_name,
        p.name,
        p.email,
        p.linkedin_url,
        p.phone,
        p.owner_name,
        p.owner_team_name,
        ownershipStatusLabel(p.ownership_status),
        commercialStateLabel(p.commercial_state),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return hay.includes(q)
    })
  }, [prospects, search, statusFilter, commercialFilter])

  async function runAction(prospectId, action) {
    setBusyId(prospectId)
    try {
      setError(null)
      await action()
      await loadData()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  function openReassign(prospect) {
    setReassignTarget(prospect)
    setReassignUserId('')
    setReassignOpen(true)
  }

  const viewerRoleNorm = normalizeRole(viewerRole)
  const isAdminViewer = isCompanyAdmin({ role: viewerRoleNorm })

  function showClaimButton(prospect) {
    if (!prospect.can_claim) {
      return false
    }
    return CLAIMABLE_OWNERSHIP_STATUSES.has(prospect.ownership_status)
  }

  function showReleaseButton(prospect) {
    return isAdminViewer && prospect.can_release
  }

  function showReassignButton(prospect) {
    return isAdminViewer && prospect.can_reassign
  }

  function showCompleteOutreachButton(prospect) {
    return prospect.can_complete_outreach
  }

  function completeOutreachLabel(prospect) {
    return prospect.outreach_prep_action === 'complete' ? 'Completar datos' : 'Enriquecer'
  }

  function showGenerateSequenceButton(prospect) {
    return prospect.can_generate_sequence
  }

  function showViewSequenceButton(prospect) {
    return prospect.can_view_sequence
  }

  function showStartSequenceButton(prospect) {
    return prospect.can_start_sequence
  }

  function openOutreach(prospect, mode) {
    setOutreachProspect(prospect)
    setOutreachMode(mode)
    setOutreachOpen(true)
  }

  function openConversation(prospect) {
    setConversationProspect(prospect)
    setConversationOpen(true)
  }

  function showConversationButton(prospect) {
    return (
      prospect.can_view_sequence ||
      prospect.ownership_status === 'en_secuencia' ||
      Boolean(prospect.last_inbound_at)
    )
  }

  function openActiveSequence(prospectId) {
    const prospect = prospects.find((p) => p.id === prospectId)
    if (prospect) {
      openOutreach(prospect, 'view')
    }
  }

  async function handleStartSequence(prospect) {
    await runAction(prospect.id, () => startProspectSequence(prospect.id))
  }

  async function handleConfirmReset() {
    if (!companyId) {
      return
    }
    setResetBusy(true)
    try {
      setError(null)
      const result = await resetTestingData(companyId)
      setResetResult(result)
      setResetOpen(false)
      await loadData()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setResetBusy(false)
    }
  }

  async function submitReassign(ev) {
    ev.preventDefault()
    if (!reassignTarget || !reassignUserId) {
      return
    }
    const toUserId = Number(reassignUserId)
    if (!Number.isFinite(toUserId) || toUserId < 1) {
      setError('Seleccioná un SDR destino')
      return
    }
    await runAction(reassignTarget.id, () =>
      reassignProspect(reassignTarget.id, toUserId),
    )
    setReassignOpen(false)
    setReassignTarget(null)
  }

  return (
    <>
      <PageHeader
        kicker="Operaciones"
        title="Prospectos"
        description={pageDescription(viewerRole)}
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          type="search"
          placeholder="Buscar por nombre, empresa, email..."
          value={search}
          onChange={(ev) => setSearch(ev.target.value)}
          className="nx-input min-w-[220px] flex-1"
        />
        <select
          value={statusFilter}
          onChange={(ev) => setStatusFilter(ev.target.value)}
          className="nx-input w-auto min-w-[10rem]"
        >
          {OWNERSHIP_FILTER_OPTIONS.map((o) => (
            <option key={o.value || 'all-own'} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          value={commercialFilter}
          onChange={(ev) => setCommercialFilter(ev.target.value)}
          className="nx-input w-auto min-w-[10rem]"
        >
          {COMMERCIAL_FILTER_OPTIONS.map((o) => (
            <option key={o.value || 'all-comm'} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-nx-border px-3 py-2 text-sm text-nx-ink">
          <input
            type="checkbox"
            checked={showSimulations}
            onChange={(ev) => setShowSimulations(ev.target.checked)}
            className="rounded border-nx-border"
          />
          Simulaciones
        </label>
        {caps.can_configure_rules ? (
          <button
            type="button"
            onClick={() => setRulesOpen(true)}
            className="nx-btn nx-btn-secondary"
          >
            Reglas
          </button>
        ) : null}
        {testingResetAvailable ? (
          <button
            type="button"
            onClick={() => {
              setResetResult(null)
              setResetOpen(true)
            }}
            className="nx-btn border border-zinc-300 bg-zinc-50 text-zinc-900 hover:bg-zinc-100"
          >
            Reiniciar pruebas
          </button>
        ) : null}
      </div>

      {resetResult ? (
        <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">
          {resetResult.detail ||
            `Reiniciados ${resetResult.prospects_reset ?? 0} prospectos.`}
        </p>
      ) : null}

      {(ctxLoading || loading) && companyId ? (
        <p className="text-sm text-nx-muted">Cargando prospectos...</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-xl border border-dashed border-nx-border bg-white px-4 py-8 text-center text-sm text-nx-muted shadow-sm">
          Sin empresa seleccionada.
        </p>
      ) : null}

      {companyId && !loading ? (
        <>
          {commercialSummary ? (
            <PageSection
              title="Pipeline comercial"
              description="Tocá un chip para filtrar la tabla."
              defaultOpen={false}
              className="mb-4"
            >
              <div className="flex flex-wrap gap-2">
                {COMMERCIAL_PIPELINE_CHIPS.map((chip) => (
                  <PipelineChip
                    key={chip.key}
                    label={chip.label}
                    value={commercialSummary[chip.summaryKey] ?? 0}
                    tone={chip.tone}
                    active={commercialFilter === chip.key}
                    onClick={() =>
                      setCommercialFilter((prev) => (prev === chip.key ? '' : chip.key))
                    }
                  />
                ))}
              </div>
            </PageSection>
          ) : null}
          <ActiveSequencesPanel
            sequences={activeSequences}
            loading={activeLoading}
            onOpenProspect={openActiveSequence}
          />
        <div className="nx-card overflow-hidden">
          <div className="border-b border-nx-border/60 px-4 py-3">
            <h2 className="text-sm font-semibold text-nx-ink">Bandeja de prospectos</h2>
            <p className="mt-0.5 text-xs text-nx-muted">
              {filtered.length} de {prospects.length} visibles con los filtros actuales.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-[1500px] w-full divide-y divide-nx-border text-sm">
              <thead className="bg-nx-card-muted text-left text-xs font-semibold uppercase tracking-wide text-nx-muted">
                <tr>
                  <th className="whitespace-nowrap px-3 py-3">Empresa</th>
                  <th className="whitespace-nowrap px-3 py-3">Nombre</th>
                  <th className="whitespace-nowrap px-3 py-3">Ownership</th>
                  <th className="whitespace-nowrap px-3 py-3">Estado comercial</th>
                  <th className="whitespace-nowrap px-3 py-3">Owner</th>
                  <th className="whitespace-nowrap px-3 py-3">Secuencia</th>
                  <th className="whitespace-nowrap px-3 py-3">Próx. toque</th>
                  <th className="whitespace-nowrap px-3 py-3">Últ. toque</th>
                  <th className="whitespace-nowrap px-3 py-3">Inicio</th>
                  <th className="whitespace-nowrap px-3 py-3">Fin</th>
                  <th className="whitespace-nowrap px-3 py-3">Lib. estimada</th>
                  <th className="whitespace-nowrap px-3 py-3">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-nx-border text-nx-ink">
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={12} className="px-4 py-10 text-center text-nx-muted">
                      No hay prospectos para mostrar.
                    </td>
                  </tr>
                ) : (
                  filtered.map((p) => {
                    const busy = busyId === p.id
                    const hasActions =
                      showClaimButton(p) ||
                      showCompleteOutreachButton(p) ||
                      showGenerateSequenceButton(p) ||
                      showViewSequenceButton(p) ||
                      showConversationButton(p) ||
                      showStartSequenceButton(p) ||
                      showReleaseButton(p) ||
                      showReassignButton(p)
                    return (
                      <tr key={p.id} className="hover:bg-nx-card-muted/90">
                        <td className="px-3 py-3 text-nx-ink">{p.company_name || '—'}</td>
                        <td className="px-3 py-3">
                          <p className="font-medium text-nx-ink">{p.name || '—'}</p>
                          <p className="text-xs text-nx-subtle">{p.email || '—'}</p>
                        </td>
                        <td className="px-3 py-3">
                          <span
                            className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${ownershipStatusBadgeClass(p.ownership_status)}`}
                          >
                            {ownershipStatusLabel(p.ownership_status)}
                          </span>
                          {sequenceStatusSubline(p) ? (
                            <p className="mt-1 text-xs font-medium text-nx-brand">
                              {sequenceStatusSubline(p)}
                            </p>
                          ) : null}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span
                              className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${commercialStateBadgeClass(p.commercial_state)}`}
                            >
                              {p.commercial_state_label || commercialStateLabel(p.commercial_state)}
                            </span>
                            {showSimulations && p.commercial_state_is_testing ? (
                              <span
                                className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset ${testingBadgeClass()}`}
                              >
                                SIM
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-nx-muted">
                          <p>{ownerDisplayLabel(p)}</p>
                          <p className="text-xs text-nx-subtle">{p.owner_team_name || ''}</p>
                        </td>
                        <td className="px-3 py-3 text-nx-muted">
                          <p>{p.sequence_current_day_label || p.sequence_current_label || formatLastSequence(p)}</p>
                          {p.ownership_status === 'en_secuencia' && p.next_touch_label ? (
                            <p className="text-xs text-nx-subtle">{p.next_touch_label}</p>
                          ) : null}
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-nx-muted">
                          <p>{p.next_touch_label || '—'}</p>
                          <p className="text-xs">{fmtDateTime(p.next_touch_at)}</p>
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-nx-muted">
                          {fmtDateTime(p.last_touch_at)}
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-nx-muted">
                          {fmtDateTime(p.sequence_start_at || p.sequence_started_at)}
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-nx-muted">
                          {fmtDateTime(p.sequence_end_at || p.sequence_completed_at)}
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-nx-muted">
                          {fmtDateTime(p.estimated_release_at || p.released_at)}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex flex-wrap gap-1.5">
                            {showClaimButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void runAction(p.id, () => claimProspect(p.id))}
                                className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs font-medium text-red-800 hover:bg-red-100 disabled:opacity-50"
                              >
                                Tomar
                              </button>
                            ) : null}
                            {showCompleteOutreachButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => openOutreach(p, 'prepare')}
                                className="rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-xs font-medium text-zinc-900 hover:bg-zinc-100 disabled:opacity-50"
                              >
                                {completeOutreachLabel(p)}
                              </button>
                            ) : null}
                            {showGenerateSequenceButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => openOutreach(p, 'generate')}
                                className="rounded border border-nx-brand/30 bg-nx-brand/5 px-2 py-1 text-xs font-medium text-nx-brand hover:bg-nx-brand/10 disabled:opacity-50"
                              >
                                Generar Secuencia
                              </button>
                            ) : null}
                            {showViewSequenceButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => openOutreach(p, 'view')}
                                className="rounded border border-nx-border px-2 py-1 text-xs font-medium text-nx-ink hover:bg-nx-card-muted disabled:opacity-50"
                              >
                                Ver Secuencia
                              </button>
                            ) : null}
                            {showConversationButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => openConversation(p)}
                                className="rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-xs font-medium text-zinc-900 hover:bg-zinc-100 disabled:opacity-50"
                              >
                                Ver conversación
                              </button>
                            ) : null}
                            {showStartSequenceButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void handleStartSequence(p)}
                                className="nx-btn nx-btn-primary px-2 py-1 text-xs"
                              >
                                Iniciar Secuencia
                              </button>
                            ) : null}
                            {showReleaseButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() =>
                                  void runAction(p.id, () => releaseProspect(p.id))
                                }
                                className="rounded border border-nx-border px-2 py-1 text-xs hover:bg-nx-card-muted disabled:opacity-50"
                              >
                                Liberar
                              </button>
                            ) : null}
                            {showReassignButton(p) ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => openReassign(p)}
                                className="rounded border border-nx-border px-2 py-1 text-xs hover:bg-nx-card-muted disabled:opacity-50"
                              >
                                Reasignar
                              </button>
                            ) : null}
                            {!hasActions ? (
                              <span className="text-xs text-nx-subtle">—</span>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
          <p className="border-t border-nx-border px-4 py-2 text-xs text-nx-subtle">
            {filtered.length} prospecto{filtered.length === 1 ? '' : 's'}
            {statusFilter ? ` · ownership: ${ownershipStatusLabel(statusFilter)}` : ''}
            {commercialFilter ? ` · comercial: ${commercialStateLabel(commercialFilter)}` : ''}
          </p>
        </div>
        </>
      ) : null}

      {resetOpen ? (
        <Modal
          title="Reiniciar entorno de pruebas"
          onClose={() => !resetBusy && setResetOpen(false)}
        >
          <div className="space-y-4 text-sm text-nx-ink">
            <p className="font-medium text-nx-ink">
              ¿Seguro que querés reiniciar todo el entorno de pruebas?
            </p>
            <p>
              Se borrarán secuencias, conversaciones simuladas, reuniones de prueba,
              ownership y estados comerciales. No se eliminan usuarios, campañas ni
              productos.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setResetOpen(false)}
                disabled={resetBusy}
                className="rounded-lg border border-nx-border px-4 py-2 text-sm disabled:opacity-60"
              >
                Cancelar
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmReset()}
                disabled={resetBusy}
                className="rounded-lg bg-zinc-600 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-60"
              >
                {resetBusy ? 'Reiniciando…' : 'Confirmar reinicio'}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {reassignOpen && reassignTarget ? (
        <Modal
          title={`Reasignar — ${reassignTarget.name}`}
          onClose={() => setReassignOpen(false)}
        >
          <form onSubmit={submitReassign} className="space-y-4">
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-nx-ink">Nuevo SDR</span>
              <select
                required
                value={reassignUserId}
                onChange={(ev) => setReassignUserId(ev.target.value)}
                className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              >
                <option value="">Seleccionar SDR</option>
                {sdrUsers.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name} ({u.email})
                  </option>
                ))}
              </select>
            </label>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setReassignOpen(false)}
                className="rounded-lg border border-nx-border px-4 py-2 text-sm"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={busyId != null}
                className="nx-btn nx-btn-primary px-4 py-2 text-sm"
              >
                Reasignar
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {rulesOpen ? (
        <Modal title="Reglas de prospectos" onClose={() => setRulesOpen(false)}>
          <div className="space-y-4 text-sm text-nx-ink">
            <p>
              Reglas de ownership entre SDRs de la empresa. Los cambios avanzados de
              configuración estarán en{' '}
              <Link to="/configuracion/integraciones" className="text-nx-brand hover:underline">
                Configuración
              </Link>
              .
            </p>
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>Libre / Liberado:</strong> cualquier SDR puede tomar el prospecto.
              </li>
              <li>
                <strong>Tomado / En secuencia:</strong> solo el owner trabaja outreach; Gerente
                puede liberar o reasignar.
              </li>
              <li>
                <strong>Finalizado:</strong> cooldown de 20 días antes de volver a quedar
                disponible automáticamente.
              </li>
              <li>
                <strong>Reasignar:</strong> solo a usuarios con rol SDR de la misma empresa.
              </li>
            </ul>
            <p className="rounded-lg bg-nx-card-muted px-3 py-2 text-xs text-nx-muted">
              Cooldown post-secuencia: 20 días (constante del sistema en esta etapa).
            </p>
          </div>
        </Modal>
      ) : null}

      <ProspectConversationModal
        prospect={conversationProspect}
        open={conversationOpen}
        includeTesting={showSimulations}
        onClose={() => {
          setConversationOpen(false)
          setConversationProspect(null)
        }}
      />

      <ProspectOutreachPanel
        prospect={outreachProspect}
        open={outreachOpen}
        mode={outreachMode}
        onClose={() => {
          setOutreachOpen(false)
          setOutreachProspect(null)
        }}
        onUpdated={async (opts = {}) => {
          if (!companyId) {
            return
          }
          const includeTesting = opts.includeTesting ?? showSimulations
          if (opts.includeTesting) {
            setShowSimulations(true)
          }
          const ws = await fetchProspectsOwnership(companyId, {
            includeTesting,
          })
          setWorkspace(ws)
          if (outreachProspect?.id) {
            const updated = (ws.prospects || []).find((x) => x.id === outreachProspect.id)
            if (updated) {
              setOutreachProspect(
                opts.commercialState
                  ? { ...updated, ...opts.commercialState }
                  : updated,
              )
            } else if (opts.commercialState) {
              setOutreachProspect((prev) =>
                prev ? { ...prev, ...opts.commercialState } : prev,
              )
            }
          }
        }}
      />
    </>
  )
}

function PipelineChip({ label, value, tone, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium ring-1 ring-inset transition ${pipelineChipToneClass(tone, active)}`}
    >
      <span>{label}</span>
      <span
        className={`rounded-full px-1.5 py-0.5 font-semibold tabular-nums ${active ? 'bg-white/20' : 'bg-white/80'}`}
      >
        {value ?? 0}
      </span>
    </button>
  )
}
