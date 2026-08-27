import { useCallback, useEffect, useMemo, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { Modal } from '../components/Modal.jsx'
import { PageHeader } from '../layout/PageHeader'
import {
  createTeam,
  fetchEquipoWorkspace,
  updateTeam,
  updateUser,
} from '../utils/api.js'
import { ROLE_LABELS, ROLES, normalizeRole } from '../data/navigation.js'

function roleLabel(role) {
  return ROLE_LABELS[normalizeRole(role)] || role
}

function statusLabel(isActive) {
  return isActive === false ? 'Inactivo' : 'Activo'
}

function emptyTeamForm() {
  return { name: '', description: '' }
}

function pageDescription(viewerRole) {
  const r = normalizeRole(viewerRole)
  if (r === ROLES.sdr) {
    return 'Tu equipo y compañeros de trabajo.'
  }
  if (r === ROLES.manager) {
    return 'Miembros de tu equipo, roles y métricas básicas.'
  }
  return 'Equipos de la empresa, usuarios y administración.'
}

/** Colores distintos por equipo (ciclo estable por id). */
const TEAM_PALETTE = [
  { accent: '#0D9488', soft: 'rgba(13, 148, 136, 0.10)', ring: 'rgba(13, 148, 136, 0.35)' },
  { accent: '#EA580C', soft: 'rgba(234, 88, 12, 0.10)', ring: 'rgba(234, 88, 12, 0.35)' },
  { accent: '#2563EB', soft: 'rgba(37, 99, 235, 0.10)', ring: 'rgba(37, 99, 235, 0.35)' },
  { accent: '#CA8A04', soft: 'rgba(202, 138, 4, 0.12)', ring: 'rgba(202, 138, 4, 0.4)' },
  { accent: '#DB2777', soft: 'rgba(219, 39, 119, 0.10)', ring: 'rgba(219, 39, 119, 0.35)' },
  { accent: '#059669', soft: 'rgba(5, 150, 105, 0.10)', ring: 'rgba(5, 150, 105, 0.35)' },
  { accent: '#DC2626', soft: 'rgba(220, 38, 38, 0.10)', ring: 'rgba(220, 38, 38, 0.35)' },
  { accent: '#0891B2', soft: 'rgba(8, 145, 178, 0.10)', ring: 'rgba(8, 145, 178, 0.35)' },
]

function teamPalette(teamId) {
  const n = Math.abs(Number(teamId) || 0)
  return TEAM_PALETTE[n % TEAM_PALETTE.length]
}

function MemberActions({
  member: m,
  caps,
  teamOptions,
  saving,
  onFieldChange,
  showTeamSelect = true,
}) {
  if (!(caps.can_assign_team || caps.can_change_role || caps.can_toggle_active)) {
    return null
  }
  return (
    <div className="flex flex-wrap gap-2">
      {caps.can_assign_team && showTeamSelect ? (
        <select
          className="rounded border border-nx-border px-2 py-1 text-xs"
          value={m.team_id ?? ''}
          disabled={saving}
          onChange={(ev) => {
            const val = ev.target.value
            void onFieldChange(m, 'team_id', val === '' ? null : Number(val))
          }}
        >
          <option value="">Sin equipo</option>
          {teamOptions.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      ) : null}
      {caps.can_change_role ? (
        <select
          className="rounded border border-nx-border px-2 py-1 text-xs"
          value={normalizeRole(m.role)}
          disabled={saving}
          onChange={(ev) => {
            void onFieldChange(m, 'role', ev.target.value)
          }}
        >
          <option value={ROLES.sdr}>SDR</option>
          <option value={ROLES.manager}>Manager</option>
          <option value={ROLES.gerente}>Director</option>
          <option value={ROLES.owner}>Owner</option>
        </select>
      ) : null}
      {caps.can_toggle_active ? (
        <button
          type="button"
          disabled={saving || m.is_self}
          onClick={() => {
            void onFieldChange(m, 'is_active', !m.is_active)
          }}
          className="rounded border border-nx-border px-2 py-1 text-xs hover:bg-nx-card-muted disabled:opacity-50"
        >
          {m.is_active ? 'Desactivar' : 'Activar'}
        </button>
      ) : null}
    </div>
  )
}

function MemberCard({ member: m, caps, teamOptions, saving, onFieldChange, showMetrics, showTeamSelect }) {
  return (
    <li className="rounded-lg border border-nx-border/80 bg-white px-3 py-2.5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-nx-ink">
            {m.name}
            {m.is_self ? (
              <span className="ml-1.5 text-xs font-normal text-nx-ink/60">(vos)</span>
            ) : null}
          </p>
          <p className="mt-0.5 text-xs text-nx-ink/70">
            {roleLabel(m.role)}
            {m.email ? ` · ${m.email}` : ''}
            {caps.show_email_all ? ` · ${statusLabel(m.is_active)}` : ''}
          </p>
          {showMetrics ? (
            <p className="mt-1 text-[11px] tabular-nums text-nx-ink/60">
              Prospectos {m.metrics?.prospects_claimed ?? 0}
              {' · '}
              Secuencias {m.metrics?.active_sequences ?? 0}
              {' · '}
              Campañas {m.metrics?.active_campaigns ?? 0}
            </p>
          ) : null}
        </div>
        <MemberActions
          member={m}
          caps={caps}
          teamOptions={teamOptions}
          saving={saving}
          onFieldChange={onFieldChange}
          showTeamSelect={showTeamSelect}
        />
      </div>
    </li>
  )
}

export default function EquipoPage() {
  const { user: authUser } = useAuth()
  const { companyId, loading: ctxLoading } = useCompany()
  const [workspace, setWorkspace] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  const [teamModalOpen, setTeamModalOpen] = useState(false)
  const [editingTeamId, setEditingTeamId] = useState(null)
  const [teamForm, setTeamForm] = useState(emptyTeamForm())
  const [openTeamIds, setOpenTeamIds] = useState(() => new Set())
  const [unassignedOpen, setUnassignedOpen] = useState(false)

  const loadWorkspace = useCallback(async () => {
    if (!companyId) {
      return
    }
    setLoading(true)
    try {
      setError(null)
      const data = await fetchEquipoWorkspace(companyId)
      setWorkspace(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setWorkspace(null)
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])

  const caps = workspace?.capabilities ?? {}
  const teams = workspace?.teams ?? []
  const members = workspace?.members ?? []
  const viewerRole = workspace?.viewer_role ?? authUser?.role

  const teamOptions = useMemo(
    () => teams.map((t) => ({ id: t.id, name: t.name })),
    [teams],
  )

  const membersByTeam = useMemo(() => {
    const map = new Map()
    for (const t of teams) map.set(t.id, [])
    for (const m of members) {
      if (m.team_id != null && map.has(m.team_id)) {
        map.get(m.team_id).push(m)
      }
    }
    return map
  }, [teams, members])

  const unassignedMembers = useMemo(
    () => members.filter((m) => m.team_id == null),
    [members],
  )

  const showMetrics = caps.show_metrics

  function toggleTeamOpen(teamId) {
    setOpenTeamIds((prev) => {
      const next = new Set(prev)
      if (next.has(teamId)) next.delete(teamId)
      else next.add(teamId)
      return next
    })
  }

  function openCreateTeam() {
    setEditingTeamId(null)
    setTeamForm(emptyTeamForm())
    setTeamModalOpen(true)
  }

  function openEditTeam(team) {
    setEditingTeamId(team.id)
    setTeamForm({
      name: team.name ?? '',
      description: team.description ?? '',
    })
    setTeamModalOpen(true)
  }

  async function handleTeamSubmit(ev) {
    ev.preventDefault()
    if (!companyId) {
      return
    }
    setSaving(true)
    try {
      setError(null)
      const payload = {
        name: teamForm.name.trim(),
        description: teamForm.description.trim() || null,
      }
      if (editingTeamId) {
        await updateTeam(editingTeamId, payload)
      } else {
        await createTeam(companyId, payload)
      }
      setTeamModalOpen(false)
      await loadWorkspace()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  async function handleUserFieldChange(member, field, value) {
    setSaving(true)
    try {
      setError(null)
      const payload = { [field]: value }
      await updateUser(member.id, payload)
      await loadWorkspace()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <PageHeader title="Equipo" description={pageDescription(viewerRole)} />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {(ctxLoading || loading) && companyId ? (
        <p className="text-sm text-nx-ink/70">Cargando equipo...</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-xl border border-dashed border-nx-border bg-white px-4 py-8 text-center text-sm text-nx-ink/70 shadow-sm">
          Sin empresa seleccionada.
        </p>
      ) : null}

      {caps.can_create_team ? (
        <div className="mb-6 flex justify-end">
          <button
            type="button"
            onClick={openCreateTeam}
            className="nx-btn nx-btn-primary px-4 py-2 text-sm"
          >
            Crear equipo
          </button>
        </div>
      ) : null}

      {teams.length > 0 ? (
        <div className="mb-8 space-y-2">
          {teams.map((team) => {
            const palette = teamPalette(team.id)
            const teamMembers = membersByTeam.get(team.id) || []
            const isOpen = openTeamIds.has(team.id)
            return (
              <section
                key={team.id}
                className="overflow-hidden rounded-lg border border-nx-border bg-white"
                style={{ borderLeftWidth: 3, borderLeftColor: palette.accent }}
              >
                <div className="flex items-stretch gap-1">
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    onClick={() => toggleTeamOpen(team.id)}
                    className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left transition hover:bg-nx-card-muted/60"
                    style={{ background: isOpen ? palette.soft : undefined }}
                  >
                    <span
                      className={[
                        'inline-flex shrink-0 text-nx-ink/50 transition-transform',
                        isOpen ? 'rotate-90' : '',
                      ].join(' ')}
                      aria-hidden
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                        <path
                          d="M9 6l6 6-6 6"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </span>
                    <span
                      className="inline-block h-2 w-2 shrink-0 rounded-full"
                      style={{ background: palette.accent }}
                      aria-hidden
                    />
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-nx-ink">
                      {team.name}
                    </span>
                    <span className="shrink-0 text-[11px] font-medium tabular-nums text-nx-ink/70">
                      {teamMembers.length}
                    </span>
                  </button>
                  {caps.can_edit_team ? (
                    <button
                      type="button"
                      onClick={() => openEditTeam(team)}
                      className="shrink-0 border-l border-nx-border px-2.5 text-[11px] font-medium text-nx-ink hover:bg-nx-card-muted"
                    >
                      Editar
                    </button>
                  ) : null}
                </div>
                {isOpen ? (
                  <div className="border-t border-nx-border px-3 py-2.5">
                    {team.description ? (
                      <p className="mb-2 text-xs text-nx-ink/80">{team.description}</p>
                    ) : null}
                    {teamMembers.length === 0 ? (
                      <p className="rounded-md border border-dashed border-nx-border px-2 py-3 text-center text-[11px] text-nx-ink/70">
                        Todavía no hay personas en este equipo.
                      </p>
                    ) : (
                      <ul className="space-y-1.5">
                        {teamMembers.map((m) => (
                          <MemberCard
                            key={m.id}
                            member={m}
                            caps={caps}
                            teamOptions={teamOptions}
                            saving={saving}
                            onFieldChange={handleUserFieldChange}
                            showMetrics={showMetrics}
                            showTeamSelect
                          />
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
              </section>
            )
          })}
        </div>
      ) : !loading && companyId ? (
        <p className="mb-6 rounded-xl border border-dashed border-nx-border bg-white px-4 py-6 text-center text-sm text-nx-ink/70 shadow-sm">
          {normalizeRole(viewerRole) === ROLES.sdr
            ? 'Aún no estás asignado a un equipo.'
            : 'No hay equipos para mostrar.'}
        </p>
      ) : null}

      {companyId && !loading ? (
        <section className="overflow-hidden rounded-lg border border-nx-border bg-white">
          <button
            type="button"
            aria-expanded={unassignedOpen}
            onClick={() => setUnassignedOpen((v) => !v)}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition hover:bg-nx-card-muted/60"
          >
            <span
              className={[
                'inline-flex shrink-0 text-nx-ink/50 transition-transform',
                unassignedOpen ? 'rotate-90' : '',
              ].join(' ')}
              aria-hidden
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                <path
                  d="M9 6l6 6-6 6"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span className="min-w-0 flex-1 text-sm font-semibold text-nx-ink">Sin equipo aún</span>
            <span className="shrink-0 text-[11px] font-medium tabular-nums text-nx-ink/70">
              {unassignedMembers.length}
            </span>
          </button>
          {unassignedOpen ? (
            <div className="border-t border-nx-border px-3 py-2.5">
              <p className="mb-2 text-xs text-nx-ink">
                Personas que todavía no estánán asignadas a ningún equipo.
              </p>
              {unassignedMembers.length === 0 ? (
                <p className="rounded-md border border-dashed border-nx-border px-2 py-3 text-center text-[11px] text-nx-ink/70">
                  Todos los usuarios visibles ya tienen equipo.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {unassignedMembers.map((m) => (
                    <MemberCard
                      key={m.id}
                      member={m}
                      caps={caps}
                      teamOptions={teamOptions}
                      saving={saving}
                      onFieldChange={handleUserFieldChange}
                      showMetrics={showMetrics}
                      showTeamSelect
                    />
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </section>
      ) : null}

      {teamModalOpen ? (
        <Modal
          title={editingTeamId ? 'Editar equipo' : 'Crear equipo'}
          onClose={() => setTeamModalOpen(false)}
        >
          <form onSubmit={handleTeamSubmit} className="space-y-4">
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-nx-ink">Nombre</span>
              <input
                required
                value={teamForm.name}
                onChange={(ev) => setTeamForm((f) => ({ ...f, name: ev.target.value }))}
                className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-nx-ink">Descripción</span>
              <textarea
                rows={3}
                value={teamForm.description}
                onChange={(ev) => setTeamForm((f) => ({ ...f, description: ev.target.value }))}
                className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              />
            </label>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setTeamModalOpen(false)}
                className="rounded-lg border border-nx-border px-4 py-2 text-sm"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={saving}
                className="nx-btn nx-btn-primary px-4 py-2 text-sm"
              >
                {saving ? 'Guardando...' : 'Guardar'}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </>
  )
}
