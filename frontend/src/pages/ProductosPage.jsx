import { useCallback, useEffect, useRef, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { Modal } from '../components/Modal.jsx'
import { PageHeader } from '../layout/PageHeader'
import {
  createProduct,
  deleteProduct,
  extractProductDocument,
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
    market_scope: 'b2b',
  }
}

function productTextBlob(product) {
  return [
    product?.name ? `Nombre\n${product.name}` : '',
    product?.description ? `Descripción\n${product.description}` : '',
    product?.value_proposition ? `Propuesta de valor\n${product.value_proposition}` : '',
    product?.target_notes || '',
  ]
    .filter(Boolean)
    .join('\n\n')
    .trim()
}

const MARKET_SCOPE_OPTIONS = [
  { value: 'b2b', label: 'B2B', hint: 'Empresas y roles' },
  { value: 'b2c', label: 'B2C', hint: 'Personas / consumidores' },
  { value: 'both', label: 'Ambas', hint: 'Campañas B2B o B2C' },
]

function MarketScopePicker({ value, onChange, disabled }) {
  return (
    <div>
      <p className="text-xs font-medium text-nx-ink">Alcance de mercado</p>
      <p className="mt-0.5 text-sm text-nx-ink">
        Define si este producto/servicio se vende a empresas, a personas, o ambos (campañas separadas).
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {MARKET_SCOPE_OPTIONS.map((opt) => {
          const active = value === opt.value
          return (
            <button
              key={opt.value}
              type="button"
              disabled={disabled}
              onClick={() => onChange(opt.value)}
              className={[
                'rounded-lg border px-3 py-2 text-left transition',
                active
                  ? 'border-nx-brand bg-nx-brand/10 text-nx-ink ring-1 ring-nx-brand/30'
                  : 'border-nx-border bg-white text-nx-ink hover:border-nx-border-strong hover:bg-nx-card-muted',
              ].join(' ')}
            >
              <span className="block text-sm font-semibold">{opt.label}</span>
              <span className="block text-[10px] opacity-80">{opt.hint}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
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
  const [fileLabel, setFileLabel] = useState('')
  const [docError, setDocError] = useState(null)
  const [extracting, setExtracting] = useState(false)
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
    setFileLabel('')
    setDocError(null)
    setModalOpen(true)
  }

  function openEdit(product) {
    setEditingId(product.id)
    setFileLabel('')
    setDocError(null)
    setForm({
      name: product.name ?? '',
      description: product.description ?? '',
      value_proposition: product.value_proposition ?? '',
      target_notes: product.target_notes ?? '',
      market_scope: product.market_scope ?? 'b2b',
    })
    setPasteDoc(productTextBlob(product))
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
      market_scope: form.market_scope || 'b2b',
    }
  }

  async function handleSubmit(ev) {
    ev.preventDefault()
    if (!companyId) {
      return
    }
    setSaving(true)
    setError(null)
    setDocError(null)
    try {
      const raw = pasteDoc.trim()
      if (raw.length < 40) {
        setDocError(
          'Agregá un párrafo sobre el producto/servicio (~40 caracteres como mínimo). Podés pegar texto o subir PDF, DOCX, TXT, etc.',
        )
        setSaving(false)
        return
      }
      const res = await interpretProductRaw(companyId, raw)
      const payload = buildPayloadFromInterpret(res)
      if (editingId == null) {
        await createProduct(companyId, payload)
      } else {
        await updateProduct(editingId, payload)
      }
      setModalOpen(false)
      setForm(emptyProductForm())
      setPasteDoc('')
      setFileLabel('')
      setDocError(null)
      await loadProducts()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (modalOpen) {
        setDocError(msg)
      } else {
        setError(msg)
      }
    } finally {
      setSaving(false)
    }
  }

  function handlePickTextFile() {
    fileInputRef.current?.click()
  }

  async function handleFileSelected(ev) {
    const file = ev.target.files?.[0]
    ev.target.value = ''
    if (!file || !companyId) {
      return
    }
    setExtracting(true)
    setDocError(null)
    try {
      const extracted = await extractProductDocument(companyId, file)
      setPasteDoc(extracted.text || '')
      setFileLabel(`${extracted.filename} · ${extracted.chars.toLocaleString('es-AR')} caracteres`)
    } catch (e) {
      setDocError(e instanceof Error ? e.message : String(e))
      setFileLabel('')
    } finally {
      setExtracting(false)
    }
  }

  async function handleDelete(product) {
    if (
      !window.confirm(
        `¿Eliminar “${product.name}”?\n\nDejará de aparecer en el catálogo y en campañas nuevas. Las campañas que ya lo usan siguen funcionando.`,
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

  const docUploadBlock = (
    <div className="rounded-lg border border-nx-border bg-nx-card-muted/80 p-3">
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.docx,.txt,.md,.markdown,.csv,.tsv,.html,.htm,.json,.xml,.rtf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown,text/csv,text/html,application/json"
        className="hidden"
        onChange={(e) => void handleFileSelected(e)}
      />
      <label className="text-xs font-medium text-nx-ink">
        Pegá información del producto/servicio o cargá un documento.
      </label>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded-lg border border-nx-border-strong bg-white px-3 py-1.5 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-60"
          onClick={handlePickTextFile}
          disabled={extracting || !companyId}
        >
          {extracting ? 'Extrayendo…' : 'Subir documento'}
        </button>
        <span className="self-center text-[11px] text-nx-ink/70">
          PDF, DOCX, TXT, MD, CSV, HTML, JSON · al guardar la IA estructura el ítem.
        </span>
      </div>
      {fileLabel ? (
        <p className="mt-2 text-[11px] font-medium text-nx-brand">{fileLabel}</p>
      ) : null}
      <textarea
        aria-invalid={Boolean(docError)}
        aria-describedby={docError ? 'product-doc-error' : undefined}
        className={[
          'mt-2 w-full rounded-lg bg-white px-3 py-2 text-sm',
          docError
            ? 'border border-red-500 focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-500/25'
            : 'border border-nx-border',
        ].join(' ')}
        rows={10}
        placeholder="Brochure, página web, bullets, FAQs… (~40 caracteres como mínimo)."
        value={pasteDoc}
        onChange={(e) => {
          setPasteDoc(e.target.value)
          setFileLabel('')
          if (docError) setDocError(null)
        }}
        disabled={extracting}
      />
      {docError ? (
        <p id="product-doc-error" className="mt-1.5 text-xs font-medium text-red-600">
          {docError}
        </p>
      ) : null}
    </div>
  )

  return (
    <>
      <PageHeader
        title="Productos/Servicios"
        description="Catálogo activo de lo que vendés con Nexus."
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-nx-ink">
          Mostrando solo ítems activos.
        </p>
        <button
          type="button"
          onClick={openCreate}
          disabled={!companyId || ctxLoading}
          className="nx-btn nx-btn-primary px-3 py-2 text-sm"
        >
          Crear producto/servicio
        </button>
      </div>

      {(ctxLoading || loading) && companyId ? (
        <p className="text-sm text-nx-muted">Cargando…</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-lg border border-dashed border-nx-border-strong bg-white px-4 py-8 text-center text-sm text-nx-muted">
          Seleccioná una empresa (header) cuando el backend responda.
        </p>
      ) : null}

      {companyId && !loading && items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-nx-border bg-white p-10 text-center text-sm text-nx-muted">
          No hay productos/servicios activos.
        </div>
      ) : null}

      {items.length ? (
        <div className="overflow-hidden rounded-xl border border-nx-border bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-nx-border text-sm">
              <thead className="bg-nx-card-muted text-left text-xs font-semibold uppercase tracking-wide text-nx-muted">
                <tr>
                  <th className="px-4 py-3">Nombre</th>
                  <th className="px-4 py-3">Mercado</th>
                  <th className="px-4 py-3">Descripción</th>
                  <th className="px-4 py-3 hidden lg:table-cell">Notas target</th>
                  <th className="px-4 py-3 text-right">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-nx-border text-nx-ink">
                {items.map((p) => (
                  <tr key={p.id}>
                    <td className="px-4 py-3 font-medium">{p.name}</td>
                    <td className="px-4 py-3">
                      <span className="rounded-md bg-nx-card-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-nx-ink">
                        {p.market_scope === 'both'
                          ? 'B2B+B2C'
                          : p.market_scope === 'b2c'
                            ? 'B2C'
                            : 'B2B'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-nx-muted">
                      <span className="line-clamp-2">{p.description}</span>
                    </td>
                    <td className="hidden px-4 py-3 text-nx-muted lg:table-cell">
                      <span className="line-clamp-2">{p.target_notes}</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          className="rounded-lg border border-nx-border px-2 py-1 text-xs font-medium hover:bg-nx-card-muted"
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
          title={editingId == null ? 'Nuevo producto/servicio' : 'Editar producto/servicio'}
          onClose={() => {
            setModalOpen(false)
            setDocError(null)
          }}
          footer={
            <>
              <button
                type="button"
                className="rounded-lg border border-nx-border px-4 py-2 text-sm hover:bg-nx-card-muted"
                onClick={() => {
                  setModalOpen(false)
                  setDocError(null)
                }}
              >
                Cancelar
              </button>
              <button
                type="submit"
                form="product-form"
                disabled={saving || extracting}
                className="nx-btn nx-btn-primary px-4 py-2 text-sm"
              >
                {saving ? 'Guardando…' : editingId == null ? 'Guardar' : 'Guardar'}
              </button>
            </>
          }
        >
          <form id="product-form" className="space-y-3" onSubmit={handleSubmit}>
            <MarketScopePicker
              value={form.market_scope || 'b2b'}
              disabled={saving || extracting}
              onChange={(market_scope) => setForm((f) => ({ ...f, market_scope }))}
            />
            {docUploadBlock}
          </form>
        </Modal>
      ) : null}
    </>
  )
}
