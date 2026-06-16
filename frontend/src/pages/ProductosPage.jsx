import { useCallback, useEffect, useRef, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { Modal } from '../components/Modal.jsx'
import { PageHeader } from '../layout/PageHeader'
import {
  createProduct,
  deleteProduct,
  fetchProducts,
  interpretProductRaw,
  updateProduct,
} from '../utils/api.js'

function emptyProductForm() {
  return {
    name: '',
    description: '',
    value_proposition: '',
    target_notes: '',
  }
}

export default function ProductosPage() {
  const { companyId, loading: ctxLoading } = useCompany()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [form, setForm] = useState(emptyProductForm())
  const [saving, setSaving] = useState(false)
  const [pasteDoc, setPasteDoc] = useState('')
  const fileInputRef = useRef(null)

  const loadProducts = useCallback(async () => {
    if (!companyId) {
      return
    }
    setLoading(true)
    try {
      setError(null)
      const list = await fetchProducts(companyId)
      setItems(Array.isArray(list) ? list : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void loadProducts()
  }, [loadProducts])

  function openCreate() {
    setEditingId(null)
    setForm(emptyProductForm())
    setPasteDoc('')
    setModalOpen(true)
  }

  function openEdit(product) {
    setEditingId(product.id)
    setPasteDoc('')
    setForm({
      name: product.name ?? '',
      description: product.description ?? '',
      value_proposition: product.value_proposition ?? '',
      target_notes: product.target_notes ?? '',
    })
    setModalOpen(true)
  }

  function buildPayloadFromInterpret(res) {
    const extra = [
      res?.target_notes,
      res?.use_cases ? `Casos de uso\n${res.use_cases}` : '',
      res?.benefits ? `Beneficios\n${res.benefits}` : '',
      res?.pain_points ? `Pain points\n${res.pain_points}` : '',
      res?.objections ? `Objeciones\n${res.objections}` : '',
      res?.recommended_tone ? `Tono\n${res.recommended_tone}` : '',
    ]
      .filter(Boolean)
      .join('\n\n')
    return {
      name: (res?.suggested_name ?? '').trim() || 'Producto',
      description: res?.description ?? '',
      value_proposition: res?.value_proposition ?? '',
      target_notes: extra,
    }
  }

  async function handleSubmit(ev) {
    ev.preventDefault()
    if (!companyId) {
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (editingId == null) {
        const raw = pasteDoc.trim()
        if (raw.length < 40) {
          setError(
            'Agregá un párrafo sobre el producto (~40 caracteres como mínimo). Podés pegar texto o subir un .txt.',
          )
          setSaving(false)
          return
        }
        const res = await interpretProductRaw(companyId, raw)
        const payload = buildPayloadFromInterpret(res)
        await createProduct(companyId, payload)
      } else {
        await updateProduct(editingId, form)
      }
      setModalOpen(false)
      setForm(emptyProductForm())
      setPasteDoc('')
      await loadProducts()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  function handlePickTextFile() {
    fileInputRef.current?.click()
  }

  function handleFileSelected(ev) {
    const file = ev.target.files?.[0]
    ev.target.value = ''
    if (!file) {
      return
    }
    const lower = file.name.toLowerCase()
    if (!lower.endsWith('.txt')) {
      setError('Por ahora solo se admite archivo .txt. PDF y DOCX estarán disponibles próximamente.')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const text = typeof reader.result === 'string' ? reader.result : ''
      setPasteDoc(text)
      setError(null)
    }
    reader.onerror = () => {
      setError('No se pudo leer el archivo.')
    }
    reader.readAsText(file)
  }

  async function handleDelete(product) {
    if (
      !window.confirm(
        `¿Desactivar el producto “${product.name}”? Podrás recuperarlo marcándolo activo vía API en el futuro.`,
      )
    ) {
      return
    }
    setError(null)
    try {
      await deleteProduct(product.id)
      await loadProducts()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <>
      <PageHeader
        title="Productos"
        description="Catálogo activo visible para la empresa seleccionada."
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Mostrando solo productos activos.
        </p>
        <button
          type="button"
          onClick={openCreate}
          disabled={!companyId || ctxLoading}
          className="rounded-lg bg-nx-brand px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-50"
        >
          Crear producto
        </button>
      </div>

      {(ctxLoading || loading) && companyId ? (
        <p className="text-sm text-slate-500">Cargando productos...</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-600">
          Seleccioná una empresa (header) cuando el backend responda.
        </p>
      ) : null}

      {companyId && !loading && items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
          No hay productos activos.
        </div>
      ) : null}

      {items.length ? (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                <tr>
                  <th className="px-4 py-3">Nombre</th>
                  <th className="px-4 py-3">Descripción</th>
                  <th className="px-4 py-3 hidden lg:table-cell">Notas target</th>
                  <th className="px-4 py-3 text-right">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-slate-700">
                {items.map((p) => (
                  <tr key={p.id}>
                    <td className="px-4 py-3 font-medium">{p.name}</td>
                    <td className="px-4 py-3 text-slate-600">
                      <span className="line-clamp-2">{p.description}</span>
                    </td>
                    <td className="hidden px-4 py-3 text-slate-600 lg:table-cell">
                      <span className="line-clamp-2">{p.target_notes}</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          className="rounded-lg border border-slate-200 px-2 py-1 text-xs font-medium hover:bg-slate-50"
                          onClick={() => openEdit(p)}
                        >
                          Editar
                        </button>
                        <button
                          type="button"
                          className="rounded-lg border border-red-100 bg-red-50 px-2 py-1 text-xs font-medium text-red-800 hover:bg-red-100"
                          onClick={() => handleDelete(p)}
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
          title={editingId == null ? 'Nuevo producto' : 'Editar producto'}
          onClose={() => setModalOpen(false)}
          footer={
            <>
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm hover:bg-slate-50"
                onClick={() => setModalOpen(false)}
              >
                Cancelar
              </button>
              <button
                type="submit"
                form="product-form"
                disabled={saving}
                className="rounded-lg bg-nx-brand px-4 py-2 text-sm font-medium text-white hover:bg-nx-brand-hover disabled:opacity-60"
              >
                {saving ? 'Guardando…' : editingId == null ? 'Guardar producto' : 'Guardar'}
              </button>
            </>
          }
        >
          <form id="product-form" className="space-y-3" onSubmit={handleSubmit}>
            {editingId == null ? (
              <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-3">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,text/plain"
                  className="hidden"
                  onChange={handleFileSelected}
                />
                <label className="text-xs font-medium text-slate-700">
                  Pegá información del producto o cargá un archivo (.txt).
                </label>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-50"
                    onClick={handlePickTextFile}
                  >
                    Subir archivo .txt
                  </button>
                  <span className="self-center text-[11px] text-slate-500">
                    PDF y DOCX próximamente · al guardar procesamos el contenido automáticamente.
                  </span>
                </div>
                <textarea
                  className="mt-2 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
                  rows={10}
                  placeholder="Brochure, página web, bullets, FAQs… (~40 caracteres como mínimo)."
                  value={pasteDoc}
                  onChange={(e) => setPasteDoc(e.target.value)}
                />
              </div>
            ) : (
              <>
                <div>
                  <label className="text-xs font-medium text-slate-600">Nombre</label>
                  <input
                    required
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                    value={form.name}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, name: e.target.value }))
                    }
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-slate-600">
                    Descripción
                  </label>
                  <textarea
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                    rows={4}
                    value={form.description}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, description: e.target.value }))
                    }
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-slate-600">
                    Notas complementarias (público objetivo, tono, etc.)
                  </label>
                  <textarea
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                    rows={4}
                    value={form.target_notes}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, target_notes: e.target.value }))
                    }
                  />
                </div>
              </>
            )}
          </form>
        </Modal>
      ) : null}
    </>
  )
}
