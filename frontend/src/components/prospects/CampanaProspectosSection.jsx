import { useCallback, useEffect, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { AlertBanner } from '../AlertBanner.jsx'
import { ProspectStatusBadge } from './ProspectStatusBadge.jsx'
import {
  acceptProspectMeetingSuggestion,
  bulkCreateCampaignProspects,
  createCampaignProspect,
  deleteProspect,
  fetchProspectConversation,
  generateProspectFollowupNow,
  generateNextProspectReply,
  markProspectFollowupSent,
  reanalyzeProspectState,
  reprogramProspectFollowup,
  sendProspectFollowupSimulated,
  fetchCampaignProspects,
  fetchServerHealth,
  patchProspect,
  simulateCampaignProspects,
} from '../../utils/api.js'
import { notifyMeetingsChanged } from '../../hooks/useMeetingsPending.js'
import { clearProspectExtensionWatch } from '../../utils/clearProspectExtensionWatch.js'

function channelLabel(ch) {
  const m = { linkedin: 'LinkedIn', email: 'Email', whatsapp: 'WhatsApp' }
  return m[ch] || ch || '—'
}

const STATUS_OPTIONS = [
  { value: 'imported', label: 'Importado' },
  { value: 'compatible', label: 'Compatible' },
  { value: 'not_compatible', label: 'No compatible' },
  { value: 'contacted', label: 'Contactado' },
  { value: 'replied', label: 'Respondió' },
  { value: 'interested', label: 'Interesado' },
  { value: 'not_interested', label: 'No interesado' },
  { value: 'meeting_booked', label: 'Reunión agendada' },
  { value: 'failed', label: 'Falló' },
]

function ScoreBadge({ value }) {
  const v = Math.round(Math.max(0, Math.min(100, Number(value) || 0)))
  return (
    <span className="shrink-0 rounded-md border border-nx-border bg-nx-card-muted px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-nx-ink">
      {v}
    </span>
  )
}

function ScoreBar({ value, tone = 'neutral' }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0))
  const fill =
    tone === 'interest'
      ? 'bg-zinc-600'
      : 'bg-nx-brand'
  return (
    <div className="flex items-center gap-2 min-w-[7.5rem]">
      <ScoreBadge value={v} />
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-nx-border">
        <div className={`h-full rounded-full ${fill}`} style={{ width: `${v}%` }} />
      </div>
      <span className="tabular-nums text-[10px] text-nx-muted">{v}%</span>
    </div>
  )
}

function parseBulkProspectLines(text) {
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
  const out = []
  lines.forEach((line, idx) => {
    const parts = line.split('|').map((p) => p.trim())
    if (parts.length < 7) {
      throw new Error(
        `Línea ${idx + 1}: esperamos 7 columnas separadas por | (Nombre | Empresa | Rol | Industria | País | LinkedIn URL | Email).`,
      )
    }
    out.push({
      name: parts[0],
      company_name: parts[1],
      role: parts[2] || null,
      industry: parts[3] || null,
      country: parts[4] || null,
      linkedin_url: parts[5] || null,
      email: parts[6] || null,
    })
  })
  return out
}

const emptyProspectForm = () => ({
  name: '',
  company_name: '',
  role: '',
  industry: '',
  country: '',
  linkedin_url: '',
  email: '',
  phone: '',
  notes: '',
})

export function CampanaProspectosSection({ campaignId, freeze = false, reloadKey = 0 }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [addOpen, setAddOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const [simulateBusy, setSimulateBusy] = useState(false)
  const [simDisabled, setSimDisabled] = useState(false)

  const [form, setForm] = useState(emptyProspectForm())
  const [editRow, setEditRow] = useState(null)
  const [editForm, setEditForm] = useState(emptyProspectForm())
  const [recalc, setRecalc] = useState(false)
  const [conversationRow, setConversationRow] = useState(null)
  const [conversationMessages, setConversationMessages] = useState([])
  const [conversationLoading, setConversationLoading] = useState(false)
  const [conversationBusy, setConversationBusy] = useState(false)
  const [followupPreview, setFollowupPreview] = useState('')

  const load = useCallback(async () => {
    if (!campaignId || freeze) {
      setRows([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await fetchCampaignProspects(campaignId)
      setRows(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [campaignId, freeze])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  useEffect(() => {
    void fetchServerHealth()
      .then((h) => setSimDisabled(Boolean(h?.outreach_simulation_disabled)))
      .catch(() => setSimDisabled(false))
  }, [])

  async function handleAdd(ev) {
    ev.preventDefault()
    setError(null)
    try {
      await createCampaignProspect(campaignId, {
        name: form.name.trim(),
        company_name: form.company_name.trim(),
        role: form.role.trim() || null,
        industry: form.industry.trim() || null,
        country: form.country.trim() || null,
        linkedin_url: form.linkedin_url.trim() || null,
        email: form.email.trim() || null,
        phone: form.phone.trim() || null,
        notes: form.notes.trim() || null,
      })
      setAddOpen(false)
      setForm(emptyProspectForm())
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleBulk(ev) {
    ev.preventDefault()
    setError(null)
    try {
      const prospects = parseBulkProspectLines(bulkText)
      await bulkCreateCampaignProspects(campaignId, prospects)
      setImportOpen(false)
      setBulkText('')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleSimulate() {
    if (simDisabled) {
      setError('Simulación deshabilitada en el servidor (NEXUS_REAL_MODE o NEXUS_DISABLE_OUTREACH_SIMULATION).')
      return
    }
    setError(null)
    setSimulateBusy(true)
    try {
      await simulateCampaignProspects(campaignId, {})
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSimulateBusy(false)
    }
  }

  async function handleDelete(p) {
    if (!window.confirm(`¿Eliminar a ${p.name} de esta campaña?`)) {
      return
    }
    setError(null)
    try {
      await deleteProspect(p.id)
      clearProspectExtensionWatch(p.id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleStatus(id, status) {
    setError(null)
    try {
      await patchProspect(id, { status })
      if (status === 'not_interested') {
        clearProspectExtensionWatch(id)
      }
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function openEdit(p) {
    setEditRow(p)
    setRecalc(false)
    setEditForm({
      name: p.name ?? '',
      company_name: p.company_name ?? '',
      role: p.role ?? '',
      industry: p.industry ?? '',
      country: p.country ?? '',
      linkedin_url: p.linkedin_url ?? '',
      email: p.email ?? '',
      phone: p.phone ?? '',
      notes: p.notes ?? '',
    })
  }

  async function handleEditSave(ev) {
    ev.preventDefault()
    if (!editRow) {
      return
    }
    setError(null)
    try {
      await patchProspect(editRow.id, {
        name: editForm.name.trim(),
        company_name: editForm.company_name.trim(),
        role: editForm.role.trim() || null,
        industry: editForm.industry.trim() || null,
        country: editForm.country.trim() || null,
        linkedin_url: editForm.linkedin_url.trim() || null,
        email: editForm.email.trim() || null,
        phone: editForm.phone.trim() || null,
        notes: editForm.notes.trim() || null,
        recalculate_scores: recalc,
      })
      setEditRow(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function openConversation(row) {
    setConversationRow(row)
    setConversationMessages([])
    setFollowupPreview('')
    setConversationLoading(true)
    setError(null)
    try {
      const items = await fetchProspectConversation(row.id)
      setConversationMessages(Array.isArray(items) ? items : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationLoading(false)
    }
  }

  async function handleGenerateNextReply() {
    if (!conversationRow) {
      return
    }
    setConversationBusy(true)
    setError(null)
    try {
      await generateNextProspectReply(conversationRow.id)
      const items = await fetchProspectConversation(conversationRow.id)
      setConversationMessages(Array.isArray(items) ? items : [])
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationBusy(false)
    }
  }

  async function handleGenerateFollowupNow() {
    if (!conversationRow) return
    setConversationBusy(true)
    setError(null)
    try {
      const res = await generateProspectFollowupNow(conversationRow.id)
      setFollowupPreview(res?.message || '')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationBusy(false)
    }
  }

  async function handleSendFollowupNow() {
    if (!conversationRow) return
    if (simDisabled) {
      setError(
        'Follow-up simulado deshabilitado en el servidor (modo real). Usá «Enviar email real» en Centro de outreach.',
      )
      return
    }
    setConversationBusy(true)
    setError(null)
    try {
      await sendProspectFollowupSimulated(conversationRow.id)
      const items = await fetchProspectConversation(conversationRow.id)
      setConversationMessages(Array.isArray(items) ? items : [])
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationBusy(false)
    }
  }

  async function handleMarkFollowupSent() {
    if (!conversationRow) return
    setConversationBusy(true)
    setError(null)
    try {
      await markProspectFollowupSent(conversationRow.id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationBusy(false)
    }
  }

  async function handleReprogramFollowup() {
    if (!conversationRow) return
    const raw = window.prompt('Reprogramar follow-up en cuántos días?', '3')
    if (raw == null) return
    const days = Math.max(0, Math.min(30, Number(raw) || 3))
    setConversationBusy(true)
    setError(null)
    try {
      await reprogramProspectFollowup(conversationRow.id, days)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationBusy(false)
    }
  }

  async function handleReanalyzeState() {
    if (!conversationRow) return
    setConversationBusy(true)
    setError(null)
    try {
      await reanalyzeProspectState(conversationRow.id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationBusy(false)
    }
  }

  async function handleAcceptMeetingSuggestion() {
    if (!conversationRow) return
    setConversationBusy(true)
    setError(null)
    try {
      await acceptProspectMeetingSuggestion(conversationRow.id)
      setConversationRow(null)
      notifyMeetingsChanged({ accepted: true })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setConversationBusy(false)
    }
  }

  const inputCls =
    'mt-1 w-full rounded-lg border border-nx-border bg-white px-3 py-2 text-sm text-nx-ink shadow-sm placeholder:text-nx-subtle focus:border-nx-subtle focus:outline-none focus:ring-2 focus:ring-nx-subtle/25'
  const btnGhost =
    'rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40'
  const btnPrimary = 'nx-btn nx-btn-primary px-3 py-2 text-xs'

  return (
    <section className="rounded-xl border border-nx-border bg-white p-4 shadow-sm shadow-nx-ink/5 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-nx-ink">Prospectos</h2>
          <p className="mt-1 text-xs text-nx-muted max-w-xl leading-relaxed">
            Alta manual o importación tipo CSV (separador <strong>|</strong>). Al iniciar la secuencia,
            Nexus enriquece email, LinkedIn y teléfono según el plan de la campaña.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={freeze || simulateBusy || !campaignId}
            className={btnGhost}
            onClick={() => setAddOpen(true)}
          >
            Agregar prospecto
          </button>
          <button
            type="button"
            disabled={freeze || !campaignId}
            className={btnGhost}
            onClick={() => setImportOpen(true)}
          >
            Importar varios
          </button>
          {import.meta.env.DEV && !simDisabled ? (
            <button
              type="button"
              disabled={freeze || simulateBusy || !campaignId || simDisabled}
              className={btnPrimary}
              title={
                simDisabled
                  ? 'Desactivado: NEXUS_REAL_MODE o NEXUS_DISABLE_OUTREACH_SIMULATION en el API.'
                  : undefined
              }
              onClick={() => void handleSimulate()}
            >
              {simulateBusy ? 'Simulando…' : 'Simular prospectos'}
            </button>
          ) : null}
        </div>
      </div>

      <AlertBanner message={freeze ? null : error} onDismiss={() => setError(null)} />

      {freeze ? (
        <p className="text-xs text-zinc-800">
          Seleccioná la empresa correcta en el header para operar estos prospectos.
        </p>
      ) : null}

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando prospectos…</p>
      ) : null}

      {!freeze && !loading && rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-nx-border px-4 py-10 text-center text-sm text-nx-muted">
          Todavía no hay prospectos. Agregá manualmente o importá un bloque pegado desde el portapapeles.
        </div>
      ) : null}

      {!freeze && rows.length ? (
        <div className="overflow-x-auto rounded-xl border border-nx-border">
          <table className="min-w-[1680px] w-full divide-y divide-nx-border text-sm">
            <thead className="bg-nx-card-muted text-left text-[11px] font-semibold uppercase tracking-wide text-nx-muted">
              <tr>
                <th className="px-4 py-2">Nombre</th>
                <th className="px-4 py-2">Empresa</th>
                <th className="px-4 py-2">Rol</th>
                <th className="px-4 py-2">País</th>
                <th className="min-w-[6rem] px-4 py-2">Canal sugerido</th>
                <th className="min-w-[10rem] max-w-[12rem] px-4 py-2">Motivo canal</th>
                <th className="px-4 py-2">Estado</th>
                <th className="px-4 py-2 whitespace-nowrap">Interés (IA)</th>
                <th className="px-4 py-2 tabular-nums">Outbounds</th>
                <th className="min-w-[7rem] max-w-[9rem] px-4 py-2">Objeción</th>
                <th className="min-w-[9rem] px-4 py-2">Compatibilidad</th>
                <th className="min-w-[9rem] px-4 py-2">Prob. interés</th>
                <th className="px-4 py-2 min-w-[7rem]">Etapa</th>
                <th className="min-w-[16rem] px-4 py-2">Score reason / Next best action</th>
                <th className="px-4 py-2 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-nx-border text-nx-ink">
              {rows.map((p) => (
                <tr key={p.id} className="align-top hover:bg-nx-card-muted/80">
                  <td className="px-4 py-2 font-medium whitespace-nowrap text-nx-ink">
                    {p.name}
                  </td>
                  <td className="px-4 py-2 text-nx-muted max-w-[10rem] truncate">
                    {p.company_name}
                  </td>
                  <td className="px-4 py-2 text-xs">{p.role ?? '—'}</td>
                  <td className="px-4 py-2 text-xs">{p.country ?? '—'}</td>
                  <td className="px-4 py-2 text-xs font-medium text-nx-ink">
                    {channelLabel(p.preferred_channel)}
                  </td>
                  <td
                    className="px-4 py-2 text-[10px] text-nx-muted max-w-[12rem] leading-snug"
                    title={p.channel_reason ?? ''}
                  >
                    {p.channel_reason ? (
                      <span className="line-clamp-3">{p.channel_reason}</span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <div className="space-y-1">
                      <ProspectStatusBadge status={p.status} />
                      <select
                        className="mt-1 w-full max-w-[11rem] rounded border border-nx-border bg-white px-2 py-1 text-[11px] text-nx-ink"
                        value={p.status}
                        onChange={(e) => handleStatus(p.id, e.target.value)}
                      >
                        {STATUS_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </td>
                  <td className="px-4 py-2 text-xs capitalize text-nx-ink">
                    {p.interest_level ?? '—'}
                  </td>
                  <td className="px-4 py-2 text-xs tabular-nums text-nx-muted">
                    {p.outreach_touch_count ?? 0}
                  </td>
                  <td
                    className="px-4 py-2 text-[10px] text-nx-muted max-w-[9rem] truncate"
                    title={p.objection_type ?? ''}
                  >
                    {p.objection_type ?? '—'}
                  </td>
                  <td className="px-4 py-2">
                    <ScoreBar value={p.compatibility_score} />
                  </td>
                  <td className="px-4 py-2">
                    <ScoreBar value={p.interest_probability} tone="interest" />
                  </td>
                  <td className="px-4 py-2 text-[10px] font-mono text-nx-muted capitalize max-w-[6rem] truncate" title={p.pipeline_stage}>
                    {p.pipeline_stage ?? '—'}
                  </td>
                  <td className="px-4 py-2">
                    <p className="max-w-[16rem] truncate text-[11px] text-nx-muted" title={p.score_reason ?? ''}>
                      {p.score_reason ?? '—'}
                    </p>
                    <p className="max-w-[16rem] truncate text-[11px] text-nx-ink" title={p.next_best_action ?? ''}>
                      {p.next_best_action ?? '—'}
                    </p>
                  </td>
                  <td className="px-4 py-2 text-right whitespace-nowrap">
                    <button
                      type="button"
                      className="mr-1 rounded border border-nx-border px-2 py-1 text-[11px] font-semibold text-nx-ink hover:bg-nx-card-muted"
                      onClick={() => void openConversation(p)}
                    >
                      Ver conversación
                    </button>
                    <button
                      type="button"
                      className="mr-1 rounded border border-nx-border px-2 py-1 text-[11px] font-semibold text-nx-ink hover:bg-nx-card-muted"
                      onClick={() => openEdit(p)}
                    >
                      Editar
                    </button>
                    <button
                      type="button"
                      className="rounded border border-red-100 bg-red-50 px-2 py-1 text-[11px] font-semibold text-red-800 hover:bg-red-100"
                      onClick={() => handleDelete(p)}
                    >
                      Eliminar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {addOpen ? (
        <Modal
          title="Agregar prospecto"
          onClose={() => setAddOpen(false)}
          footer={
            <>
              <button
                type="button"
                className="rounded-lg border border-nx-border bg-white px-4 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
                onClick={() => setAddOpen(false)}
              >
                Cancelar
              </button>
              <button
                type="submit"
                form="add-prospect-form"
                className="nx-btn nx-btn-primary px-4 py-2 text-sm"
              >
                Guardar
              </button>
            </>
          }
        >
          <form id="add-prospect-form" className="grid gap-2 text-sm sm:grid-cols-2" onSubmit={handleAdd}>
            {[
              ['name', 'Nombre *', 'text', true],
              ['company_name', 'Empresa *', 'text', true],
              ['role', 'Rol', 'text'],
              ['industry', 'Industria', 'text'],
              ['country', 'País', 'text'],
              ['linkedin_url', 'LinkedIn URL', 'text'],
              ['email', 'Email', 'text'],
              ['phone', 'Teléfono', 'tel'],
            ].map(([key, lab, tp, req]) => (
              <div key={key}>
                <label className="text-xs font-medium text-nx-ink">{lab}</label>
                <input
                  type={tp}
                  required={!!req}
                  className={inputCls}
                  value={form[key]}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                />
              </div>
            ))}
            <div className="md:col-span-2">
              <label className="text-xs font-medium text-nx-ink">Notas</label>
              <textarea
                rows={3}
                className={inputCls}
                value={form.notes}
                onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              />
            </div>
          </form>
        </Modal>
      ) : null}

      {importOpen ? (
        <Modal
          title="Importar prospectos"
          onClose={() => setImportOpen(false)}
          footer={
            <>
              <button
                type="button"
                className="rounded-lg border border-nx-border bg-white px-4 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
                onClick={() => setImportOpen(false)}
              >
                Cancelar
              </button>
              <button
                type="submit"
                form="bulk-import-form"
                className="nx-btn nx-btn-primary px-4 py-2 text-sm"
              >
                Importar
              </button>
            </>
          }
        >
          <form id="bulk-import-form" className="space-y-3" onSubmit={handleBulk}>
            <p className="text-xs text-nx-muted leading-relaxed">
              Una línea por prospecto, columnas separadas por <strong>|</strong>:
              <br />
              <span className="font-mono text-[11px] rounded bg-nx-card-muted px-1 py-0.5 text-nx-ink">
                Nombre | Empresa | Rol | Industria | País | LinkedIn URL | Email
              </span>
            </p>
            <textarea
              required
              rows={8}
              className="w-full rounded-lg border border-nx-border bg-white px-3 py-2 font-mono text-xs text-nx-ink shadow-sm focus:border-nx-subtle focus:outline-none focus:ring-2 focus:ring-nx-subtle/25"
              value={bulkText}
              placeholder={'María Pérez | Acme Corp | VP Sales | Software | España | https://linkedin.com/in/maria-demo | maria.demo@corp.test'}
              onChange={(e) => setBulkText(e.target.value)}
            />
          </form>
        </Modal>
      ) : null}

      {editRow ? (
        <Modal
          title="Editar prospecto"
          onClose={() => setEditRow(null)}
          footer={
            <>
              <button
                type="button"
                className="rounded-lg border border-nx-border bg-white px-4 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
                onClick={() => setEditRow(null)}
              >
                Cancelar
              </button>
              <button
                type="submit"
                form="edit-prospect-form"
                className="nx-btn nx-btn-primary px-4 py-2 text-sm"
              >
                Guardar
              </button>
            </>
          }
        >
          <form id="edit-prospect-form" className="grid gap-2 text-sm sm:grid-cols-2" onSubmit={handleEditSave}>
            {[
              ['name', 'Nombre *', true],
              ['company_name', 'Empresa *', true],
              ['role', 'Rol'],
              ['industry', 'Industria'],
              ['country', 'País'],
              ['linkedin_url', 'LinkedIn URL'],
              ['email', 'Email'],
              ['phone', 'Teléfono'],
            ].map(([key, lab, req]) => (
              <div key={key}>
                <label className="text-xs font-medium text-nx-ink">{lab}</label>
                <input
                  required={!!req}
                  className={inputCls}
                  value={editForm[key]}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, [key]: e.target.value }))
                  }
                />
              </div>
            ))}
            <div className="md:col-span-2">
              <label className="text-xs font-medium text-nx-ink">Notas</label>
              <textarea
                rows={3}
                className={inputCls}
                value={editForm.notes}
                onChange={(e) =>
                  setEditForm((f) => ({ ...f, notes: e.target.value }))
                }
              />
            </div>
            <div className="sm:col-span-2 flex items-center gap-2">
              <input
                id="recalc"
                type="checkbox"
                checked={recalc}
                onChange={(e) => setRecalc(e.target.checked)}
              />
              <label htmlFor="recalc" className="text-xs text-nx-muted">
                Recalcular scoring (compatibilidad / probabilidad / estado compatible o no compatible según ICP)
              </label>
            </div>
          </form>
        </Modal>
      ) : null}

      {conversationRow ? (
        <Modal
          title={`Conversación · ${conversationRow.name}`}
          onClose={() => setConversationRow(null)}
          footer={
            <>
              <button
                type="button"
                disabled={conversationLoading || conversationBusy}
                className="rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
                onClick={() => void handleGenerateFollowupNow()}
              >
                Generar follow-up ahora
              </button>
              <button
                type="button"
                disabled={conversationLoading || conversationBusy || simDisabled}
                className="rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
                title={
                  simDisabled
                    ? 'Desactivado en modo real; usá Gmail send desde Centro de outreach.'
                    : undefined
                }
                onClick={() => void handleSendFollowupNow()}
              >
                Enviar follow-up simulado
              </button>
              <button
                type="button"
                disabled={conversationLoading || conversationBusy}
                className="rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
                onClick={() => void handleMarkFollowupSent()}
              >
                Marcar como enviado
              </button>
              <button
                type="button"
                disabled={conversationLoading || conversationBusy}
                className="rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
                onClick={() => void handleReprogramFollowup()}
              >
                Reprogramar
              </button>
              <button
                type="button"
                disabled={conversationLoading || conversationBusy}
                className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs font-semibold text-zinc-900 hover:bg-zinc-100 disabled:opacity-40"
                onClick={() => void handleReanalyzeState()}
              >
                Reanalizar estado
              </button>
              <button
                type="button"
                disabled={
                  conversationLoading ||
                  conversationBusy ||
                  !(
                    conversationRow.meeting_suggestion_pending ||
                    conversationRow.interest_level === 'high'
                  )
                }
                className="rounded-lg border border-red-300 bg-red-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-red-700 disabled:opacity-40"
                onClick={() => void handleAcceptMeetingSuggestion()}
              >
                Aceptar reunión / Agendar en Calendar
              </button>
              <button
                type="button"
                disabled={conversationLoading || conversationBusy}
                className="nx-btn nx-btn-primary px-4 py-2 text-sm"
                onClick={() => void handleGenerateNextReply()}
              >
                {conversationBusy ? 'Generando…' : 'Generar siguiente respuesta IA'}
              </button>
              <button
                type="button"
                className="rounded-lg border border-nx-border bg-white px-4 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
                onClick={() => setConversationRow(null)}
              >
                Cerrar
              </button>
            </>
          }
        >
          <div className="space-y-3">
            {conversationLoading ? (
              <p className="text-sm text-nx-muted">Cargando conversación…</p>
            ) : null}
            {!conversationLoading &&
            (conversationRow.meeting_suggestion_pending ||
              conversationRow.interest_level === 'high') ? (
              <div className="rounded-xl border border-red-200 bg-gradient-to-r from-red-50 to-white px-4 py-3 text-sm text-red-950 shadow-sm">
                <p className="font-semibold text-red-900">La IA puede estar sugiriendo coordinar una llamada</p>
                <p className="mt-1 text-xs text-red-900/85">
                  Se crea un registro de reunión con timezone, duración sugerida y franjas placeholder (Google
                  Calendar en una fase posterior).
                </p>
              </div>
            ) : null}
            {!conversationLoading && conversationMessages.length === 0 ? (
              <div className="rounded-lg border border-dashed border-nx-border px-3 py-8 text-center text-sm text-nx-muted">
                Todavía no hay mensajes para este prospecto.
              </div>
            ) : null}
            {!conversationLoading && conversationMessages.length > 0 ? (
              <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
                {conversationMessages.map((m) => {
                  const inbound = m.direction === 'inbound'
                  return (
                    <div
                      key={m.id}
                      className={`flex ${inbound ? 'justify-start' : 'justify-end'}`}
                    >
                      <div
                        className={`max-w-[85%] rounded-xl px-3 py-2 text-sm shadow-sm ${
                          inbound
                            ? 'border border-nx-border bg-white text-nx-ink'
                            : 'bg-nx-brand text-white'
                        }`}
                      >
                        <p>{m.message}</p>
                        <p
                          className={`mt-1 text-[10px] ${
                            inbound ? 'text-nx-muted' : 'text-nx-border'
                          }`}
                        >
                          {m.sender_type} · {m.channel} ·{' '}
                          {new Date(m.created_at).toLocaleString()}
                          {m.sender_type === 'ai' ? ' · Generado por IA' : ''}
                        </p>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : null}
            {followupPreview ? (
              <div className="rounded-lg border border-nx-border bg-nx-card-muted px-3 py-2 text-sm text-nx-ink">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">
                  Preview follow-up
                </p>
                <p className="mt-1">{followupPreview}</p>
              </div>
            ) : null}
          </div>
        </Modal>
      ) : null}
    </section>
  )
}
