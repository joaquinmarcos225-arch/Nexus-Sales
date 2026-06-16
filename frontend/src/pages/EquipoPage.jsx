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

  const showTeamColumn = caps.show_all_teams || normalizeRole(viewerRole) !== ROLES.sdr
  const showEmailColumn = caps.show_email_all
  const showMetrics = caps.show_metrics

  return (
    <>
      <PageHeader title="Equipo" description={pageDescription(viewerRole)} />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {(ctxLoading || loading) && companyId ? (
        <p className="text-sm text-[#6b7280]">Cargando equipo...</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-xl border border-dashed border-[#e5e7eb] bg-white px-4 py-8 text-center text-sm text-[#6b7280] shadow-sm">
          Sin empresa seleccionada.
        </p>
      ) : null}

      {caps.can_create_team ? (
        <div className="mb-6 flex justify-end">
          <button
            type="button"
            onClick={openCreateTeam}
            className="rounded-lg bg-nx-brand px-4 py-2 text-sm font-medium text-white hover:bg-nx-brand/90"
          >
            Crear equipo
          </button>
        </div>
      ) : null}

      {teams.length > 0 ? (
        <div className="mb-8 space-y-4">
          {teams.map((team) => (
            <div
              key={team.id}
              className="rounded-xl border border-[#e5e7eb] bg-white px-4 py-4 shadow-sm shadow-[#111827]/5"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-[#111827]">{team.name}</h3>
                  {team.description ? (
                    <p className="mt-1 text-sm text-[#6b7280]">{team.description}</p>
                  ) : null}
                  <p className="mt-2 text-xs text-[#9ca3af]">
                    {team.member_count} miembro{team.member_count === 1 ? '' : 's'}
                  </p>
                </div>
                {caps.can_edit_team ? (
                  <button
                    type="button"
                    onClick={() => openEditTeam(team)}
                    className="rounded-lg border border-[#e5e7eb] px-3 py-1.5 text-sm text-[#374151] hover:bg-[#f8fafc]"
                  >
                    Editar equipo
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : !loading && companyId ? (
        <p className="mb-6 rounded-xl border border-dashed border-[#e5e7eb] bg-white px-4 py-6 text-center text-sm text-[#6b7280] shadow-sm">
          {normalizeRole(viewerRole) === ROLES.sdr
            ? 'Aún no estás asignado a un equipo.'
            : 'No hay equipos para mostrar.'}
        </p>
      ) : null}

      {members.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-[#e5e7eb] bg-white shadow-sm shadow-[#111827]/5">
          <div className="overflow-x-auto">
            <table className="min-w-[760px] w-full divide-y divide-[#e5e7eb] text-sm">
              <thead className="bg-[#f8fafc] text-left text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
                <tr>
                  <th className="whitespace-nowrap px-4 py-3">Nombre</th>
                  {showEmailColumn ? (
                    <th className="whitespace-nowrap px-4 py-3">Email</th>
                  ) : null}
                  <th className="whitespace-nowrap px-4 py-3">Rol</th>
                  {showTeamColumn ? (
                    <th className="whitespace-nowrap px-4 py-3">Equipo</th>
                  ) : null}
                  {caps.show_email_all ? (
                    <th className="whitespace-nowrap px-4 py-3">Estado</th>
                  ) : null}
                  {showMetrics ? (
                    <>
                      <th className="whitespace-nowrap px-4 py-3 text-right">Prospectos</th>
                      <th className="whitespace-nowrap px-4 py-3 text-right">Secuencias</th>
                      <th className="whitespace-nowrap px-4 py-3 text-right">Campañas</th>
                    </>
                  ) : null}
                  {caps.can_assign_team || caps.can_change_role || caps.can_toggle_active ? (
                    <th className="whitespace-nowrap px-4 py-3">Acciones</th>
                  ) : null}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#e5e7eb] text-[#374151]">
                {members.map((m) => {
                  const emailVisible = caps.show_email_all || m.is_self
                  return (
                    <tr key={m.id} className="hover:bg-[#f8fafc]/90">
                      <td className="px-4 py-3 font-medium text-[#111827]">
                        {m.name}
                        {m.is_self ? (
                          <span className="ml-2 text-xs font-normal text-[#9ca3af]">(vos)</span>
                        ) : null}
                      </td>
                      {showEmailColumn ? (
                        <td className="px-4 py-3 text-[#6b7280]">
                          {emailVisible ? m.email : '—'}
                        </td>
                      ) : null}
                      <td className="px-4 py-3 text-[#6b7280]">{roleLabel(m.role)}</td>
                      {showTeamColumn ? (
                        <td className="px-4 py-3 text-[#6b7280]">{m.team_name || '—'}</td>
                      ) : null}
                      {caps.show_email_all ? (
                        <td className="px-4 py-3 text-[#6b7280]">{statusLabel(m.is_active)}</td>
                      ) : null}
                      {showMetrics ? (
                        <>
                          <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                            {m.metrics?.prospects_claimed ?? 0}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                            {m.metrics?.active_sequences ?? 0}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                            {m.metrics?.active_campaigns ?? 0}
                          </td>
                        </>
                      ) : null}
                      {caps.can_assign_team || caps.can_change_role || caps.can_toggle_active ? (
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-2">
                            {caps.can_assign_team ? (
                              <select
                                className="rounded border border-[#e5e7eb] px-2 py-1 text-xs"
                                value={m.team_id ?? ''}
                                disabled={saving}
                                onChange={(ev) => {
                                  const val = ev.target.value
                                  void handleUserFieldChange(
                                    m,
                                    'team_id',
                                    val === '' ? null : Number(val),
                                  )
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
                                className="rounded border border-[#e5e7eb] px-2 py-1 text-xs"
                                value={normalizeRole(m.role)}
                                disabled={saving}
                                onChange={(ev) => {
                                  void handleUserFieldChange(m, 'role', ev.target.value)
                                }}
                              >
                                <option value={ROLES.sdr}>SDR</option>
                                <option value={ROLES.manager}>Manager</option>
                                <option value={ROLES.gerente}>Gerente</option>
                              </select>
                            ) : null}
                            {caps.can_toggle_active ? (
                              <button
                                type="button"
                                disabled={saving || m.is_self}
                                onClick={() => {
                                  void handleUserFieldChange(m, 'is_active', !m.is_active)
                                }}
                                className="rounded border border-[#e5e7eb] px-2 py-1 text-xs hover:bg-[#f8fafc] disabled:opacity-50"
                              >
                                {m.is_active ? 'Desactivar' : 'Activar'}
                              </button>
                            ) : null}
                          </div>
                        </td>
                      ) : null}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {teamModalOpen ? (
        <Modal
          title={editingTeamId ? 'Editar equipo' : 'Crear equipo'}
          onClose={() => setTeamModalOpen(false)}
        >
          <form onSubmit={handleTeamSubmit} className="space-y-4">
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-[#374151]">Nombre</span>
              <input
                required
                value={teamForm.name}
                onChange={(ev) => setTeamForm((f) => ({ ...f, name: ev.target.value }))}
                className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-[#374151]">Descripción</span>
              <textarea
                rows={3}
                value={teamForm.description}
                onChange={(ev) => setTeamForm((f) => ({ ...f, description: ev.target.value }))}
                className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
              />
            </label>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setTeamModalOpen(false)}
                className="rounded-lg border border-[#e5e7eb] px-4 py-2 text-sm"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={saving}
                className="rounded-lg bg-nx-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
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
