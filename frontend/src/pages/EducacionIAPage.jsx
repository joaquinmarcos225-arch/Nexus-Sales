import { useCallback, useEffect, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { AiBehaviorPolicyPanel } from '../components/AiBehaviorPolicyPanel.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { Modal } from '../components/Modal.jsx'
import { PageHeader } from '../layout/PageHeader'
import {
  createAIInstruction,
  deleteAIInstruction,
  fetchAIInstructions,
  updateAIInstruction,
} from '../utils/api.js'

const BEHAVIOR_SYSTEM_TITLE = 'Nexus · Comportamiento SDR (sistema)'

export default function EducacionIAPage() {
  const { companyId, loading: ctxLoading } = useCompany()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [modalOpen, setModalOpen] = useState(false)
  const [modalMode, setModalMode] = useState('create')
  const [editingId, setEditingId] = useState(null)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [savingModal, setSavingModal] = useState(false)

  const btnPrimary =
    'rounded-lg bg-nx-brand px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-50'
  const inputClass =
    'mt-1 w-full rounded-lg border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#111827] shadow-sm placeholder:text-[#9ca3af] focus:border-[#9ca3af] focus:outline-none focus:ring-2 focus:ring-[#9ca3af]/25'

  const load = useCallback(async () => {
    if (!companyId) {
      setItems([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await fetchAIInstructions(companyId)
      setItems(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void load()
  }, [load])

  function openCreate() {
    setModalMode('create')
    setEditingId(null)
    setTitle('')
    setContent('')
    setModalOpen(true)
  }

  function openEdit(row) {
    setModalMode('edit')
    setEditingId(row.id)
    setTitle(row.title)
    setContent(row.content)
    setModalOpen(true)
  }

  async function handleToggle(row) {
    if (!companyId) {
      return
    }
    setError(null)
    try {
      await updateAIInstruction(companyId, row.id, { is_active: !row.is_active })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleDelete(row) {
    if (!companyId) {
      return
    }
    if (
      typeof window !== 'undefined' &&
      !window.confirm(`¿Eliminar la instrucción «${row.title}»?`)
    ) {
      return
    }
    setError(null)
    try {
      await deleteAIInstruction(companyId, row.id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleSaveModal(ev) {
    ev.preventDefault()
    if (!companyId) {
      return
    }
    const ti = title.trim()
    const co = content.trim()
    if (!ti || !co) {
      setError('Completá título y contenido.')
      return
    }
    setSavingModal(true)
    setError(null)
    try {
      if (modalMode === 'create') {
        await createAIInstruction(companyId, { title: ti, content: co })
      } else if (editingId != null) {
        await updateAIInstruction(companyId, editingId, { title: ti, content: co })
      }
      setModalOpen(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSavingModal(false)
    }
  }

  const userInstructions = items.filter((row) => row.title !== BEHAVIOR_SYSTEM_TITLE)

  const fmtDate = (iso) => {
    try {
      return new Date(iso).toLocaleString('es-AR', {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    } catch {
      return iso
    }
  }

  return (
    <>
      <PageHeader
        title="Educación IA"
        description="Entrená a tu SDR IA: comportamiento comercial (panel superior) e instrucciones libres que se inyectan en cada prompt."
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {companyId ? <AiBehaviorPolicyPanel companyId={companyId} onError={setError} /> : null}

      <div className="mb-4 flex flex-wrap justify-between gap-3">
        <p className="max-w-xl text-xs text-[#6b7280]">
          Instrucciones libres (abajo): tono, producto, objeciones. El comportamiento del calendario y CTAs se
          configura en el panel «Comportamiento del SDR IA».
        </p>
        <button
          type="button"
          disabled={ctxLoading || !companyId}
          className={btnPrimary}
          onClick={openCreate}
        >
          Nueva instrucción
        </button>
      </div>

      {(loading || ctxLoading) && companyId ? (
        <p className="text-sm text-[#6b7280]">Cargando…</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-xl border border-dashed border-[#e5e7eb] bg-white px-4 py-8 text-center text-sm text-[#6b7280] shadow-sm">
          Sin empresa seleccionada (revisá el backend y `/companies`).
        </p>
      ) : null}

      {!loading && companyId && userInstructions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[#e5e7eb] bg-white p-10 text-center text-sm text-[#6b7280] shadow-sm">
          No hay instrucciones todavía. Creá la primera para ajustar el estilo global de Nexus.
        </div>
      ) : null}

      {userInstructions.length ? (
        <div className="overflow-hidden rounded-xl border border-[#e5e7eb] bg-white shadow-sm shadow-[#111827]/5">
          <div className="overflow-x-auto">
            <table className="min-w-[720px] w-full divide-y divide-[#e5e7eb] text-sm">
              <thead className="bg-[#f8fafc] text-left text-[11px] font-semibold uppercase tracking-wide text-[#6b7280]">
                <tr>
                  <th className="whitespace-nowrap px-4 py-3">Activa</th>
                  <th className="whitespace-nowrap px-4 py-3">Título</th>
                  <th className="whitespace-nowrap px-4 py-3">Contenido</th>
                  <th className="whitespace-nowrap px-4 py-3">Creada</th>
                  <th className="whitespace-nowrap px-4 py-3 text-right">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#e5e7eb] text-[#374151]">
                {userInstructions.map((row) => (
                  <tr key={row.id} className="align-top hover:bg-[#f8fafc]/90">
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => void handleToggle(row)}
                        title={row.is_active ? 'Desactivar' : 'Activar'}
                        className="rounded-full border px-3 py-1 text-xs font-semibold transition-colors hover:bg-[#f1f5f9]"
                      >
                        {row.is_active ? 'Sí' : 'No'}
                      </button>
                    </td>
                    <td className="max-w-[10rem] px-4 py-3 font-medium text-[#111827]">
                      {row.title}
                    </td>
                    <td className="max-w-md px-4 py-3 text-xs leading-relaxed text-[#6b7280]">
                      <span className="line-clamp-3">{row.content}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-[#9ca3af]">
                      {fmtDate(row.created_at)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right">
                      <div className="flex flex-wrap justify-end gap-2">
                        <button
                          type="button"
                          className="rounded-lg border border-[#e5e7eb] bg-white px-2 py-1 text-[11px] font-semibold text-[#374151] hover:bg-[#f8fafc]"
                          onClick={() => openEdit(row)}
                        >
                          Editar
                        </button>
                        <button
                          type="button"
                          className="rounded-lg border border-[#fecaca] bg-white px-2 py-1 text-[11px] font-semibold text-[#b91c1c] hover:bg-[#fef2f2]"
                          onClick={() => void handleDelete(row)}
                        >
                          Eliminar
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {modalOpen ? (
        <Modal
          title={modalMode === 'create' ? 'Nueva instrucción IA' : 'Editar instrucción'}
          onClose={() => (!savingModal ? setModalOpen(false) : undefined)}
          footer={
            <>
              <button
                type="button"
                disabled={savingModal}
                className="rounded-lg border border-[#e5e7eb] bg-white px-4 py-2 text-sm font-medium text-[#374151] hover:bg-[#f8fafc]"
                onClick={() => setModalOpen(false)}
              >
                Cancelar
              </button>
              <button type="submit" form="edu-form" disabled={savingModal} className={btnPrimary}>
                {savingModal ? 'Guardando…' : 'Guardar'}
              </button>
            </>
          }
        >
          <form id="edu-form" onSubmit={handleSaveModal} className="space-y-4">
            <div>
              <label htmlFor="edu-title" className="text-xs font-medium text-[#374151]">
                Título corto (interno)
              </label>
              <input
                id="edu-title"
                className={inputClass}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
              />
            </div>
            <div>
              <label htmlFor="edu-content" className="text-xs font-medium text-[#374151]">
                Texto para el modelo (se inyecta en el prompt cuando está activa)
              </label>
              <textarea
                id="edu-content"
                required
                rows={6}
                className={`${inputClass} min-h-[7rem]`}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder='Ej.: "Evitá emojis". "Mensajes hasta 60 palabras salvo que el usuario pida detalle".
'
              />
            </div>
          </form>
        </Modal>
      ) : null}
    </>
  )
}
