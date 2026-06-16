import { useCallback, useEffect, useState } from 'react'
import {
  fetchAIBehaviorPolicy,
  fetchAIBehaviorPolicyFields,
  saveAIBehaviorPolicy,
} from '../utils/api.js'

const DEFAULT_POLICY = {
  commercial_aggressiveness: 'low',
  cta_frequency: 'rare',
  response_length: 'medium',
  technical_level: 'balanced',
  follow_up_style: 'warm',
  formality: 'neutral',
  humor: 'professional',
}

export function AiBehaviorPolicyPanel({ companyId, onError }) {
  const [policy, setPolicy] = useState(DEFAULT_POLICY)
  const [fields, setFields] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savedFlash, setSavedFlash] = useState(false)

  const inputClass =
    'mt-1 w-full rounded-lg border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#111827] shadow-sm focus:border-[#9ca3af] focus:outline-none focus:ring-2 focus:ring-[#9ca3af]/25'
  const btnPrimary =
    'rounded-lg bg-nx-brand px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-50'

  const load = useCallback(async () => {
    if (!companyId) {
      setPolicy(DEFAULT_POLICY)
      setFields([])
      return
    }
    setLoading(true)
    try {
      const [pol, meta] = await Promise.all([
        fetchAIBehaviorPolicy(companyId),
        fetchAIBehaviorPolicyFields(companyId),
      ])
      setPolicy({ ...DEFAULT_POLICY, ...(pol || {}) })
      setFields(Array.isArray(meta) ? meta : [])
    } catch (e) {
      onError?.(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId, onError])

  useEffect(() => {
    void load()
  }, [load])

  async function handleSave(ev) {
    ev.preventDefault()
    if (!companyId) {
      return
    }
    setSaving(true)
    setSavedFlash(false)
    try {
      const saved = await saveAIBehaviorPolicy(companyId, policy)
      setPolicy({ ...DEFAULT_POLICY, ...(saved || {}) })
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 2500)
    } catch (e) {
      onError?.(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  function setField(key, value) {
    setPolicy((prev) => ({ ...prev, [key]: value }))
  }

  if (!companyId) {
    return null
  }

  return (
    <section className="mb-6 rounded-xl border border-[#e5e7eb] bg-white p-5 shadow-sm shadow-[#111827]/5">
      <PolicyHeader savedFlash={savedFlash} />
      <p className="mt-1 max-w-2xl text-xs leading-relaxed text-[#6b7280]">
        Definí cómo actúa tu SDR IA: cuándo mandar el link de reunión, agresividad comercial, longitud de
        respuestas y prioridad consultiva. Por defecto el calendario solo se incluye si el prospecto lo pide o
        quiere agendar.
      </p>

      {loading ? (
        <p className="mt-4 text-sm text-[#6b7280]">Cargando comportamiento…</p>
      ) : (
        <form onSubmit={handleSave} className="mt-4 grid gap-4 sm:grid-cols-2">
          {fields.map((f) => (
            <div key={f.key}>
              <label htmlFor={`policy-${f.key}`} className="text-xs font-medium text-[#374151]">
                {f.label}
              </label>
              <select
                id={`policy-${f.key}`}
                className={inputClass}
                value={policy[f.key] ?? DEFAULT_POLICY[f.key]}
                onChange={(e) => setField(f.key, e.target.value)}
              >
                {(f.options || []).map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              {f.description ? (
                <p className="mt-1 text-[11px] text-[#9ca3af]">{f.description}</p>
              ) : null}
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
            <button type="submit" disabled={saving} className={btnPrimary}>
              {saving ? 'Guardando…' : 'Guardar comportamiento'}
            </button>
            {savedFlash ? (
              <span className="text-xs text-emerald-600">
                Listo — aplica en respuestas inbound y simulación.
              </span>
            ) : null}
          </div>
        </form>
      )}
    </section>
  )
}

function PolicyHeader({ savedFlash }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <h2 className="text-sm font-semibold text-[#111827]">Comportamiento del SDR IA</h2>
      {savedFlash ? <span className="text-xs font-medium text-emerald-600">Guardado</span> : null}
    </div>
  )
}
