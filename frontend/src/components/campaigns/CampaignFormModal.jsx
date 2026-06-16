import { useEffect, useMemo, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { AlertBanner } from '../AlertBanner.jsx'
import { createCampaign, updateCampaign } from '../../utils/api.js'
import {
  SUGGEST_AVAILABLE_HOURS,
  SUGGEST_COMPANY_SIZE,
  SUGGEST_COUNTRY,
  SUGGEST_INDUSTRY,
  SUGGEST_LANGUAGE,
  SUGGEST_ROLE,
  SUGGEST_TIMEZONE,
  SUGGEST_TONE,
} from '../../data/campaignFormSuggestions.js'
import {
  ICP_MISSING_MESSAGE,
  icpHasMinimumSignal,
  icpLooksEmpty,
} from '../../utils/icp.js'
import { DEFAULT_ALLOWED_CHANNELS } from '../../utils/campaignChannels.js'
import { CampaignChannelsField } from './CampaignChannelsField.jsx'

const inputClass =
  'mt-1 w-full rounded-lg border border-[#e5e7eb] bg-white px-3 py-2.5 text-sm text-[#111827] shadow-sm placeholder:text-[#9ca3af] focus:border-[#9ca3af] focus:outline-none focus:ring-2 focus:ring-[#9ca3af]/25'

const sectionTitle =
  'text-[11px] font-semibold uppercase tracking-[0.12em] text-[#9ca3af]'

const sectionBox =
  'rounded-xl border border-[#e5e7eb] bg-[#f8fafc]/90 p-4 shadow-sm space-y-3'

function toApiIcp(value) {
  const v = String(value ?? '').trim()
  if (icpLooksEmpty(v)) {
    return null
  }
  return v
}

function DatalistInput({ id, listId, label, value, onChange, suggestions, type = 'text', required }) {
  return (
    <div>
      <label htmlFor={id} className="text-xs font-medium text-[#374151]">
        {label}
      </label>
      <input
        id={id}
        name={id}
        type={type}
        required={required}
        className={inputClass}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        list={listId}
        autoComplete="off"
      />
      <datalist id={listId}>
        {suggestions.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
    </div>
  )
}

const defaultForm = () => ({
  name: '',
  productQuery: '',
  seller_id: '',
  target_company_size: '',
  target_industry: '',
  target_country: '',
  target_language: '',
  target_role: '',
  prospect_count: '120',
  calendar_link: '',
  timezone: '',
  available_hours: '',
  tone: '',
  allowed_channels: [...DEFAULT_ALLOWED_CHANNELS],
  autopilot_status: 'off',
  sender_name: '',
  sender_email: '',
  ai_context: '',
  followup_delay_days: '',
  max_auto_followups: '',
  inbound_reply_mode: 'draft_only',
  inbound_reply_delay_minutes: '2',
})

function formFromCampaign(c) {
  return {
    name: c.name ?? '',
    productQuery: c.product_name ?? '',
    seller_id: c.seller_id != null ? String(c.seller_id) : '',
    target_company_size: c.target_company_size ?? '',
    target_industry: c.target_industry ?? '',
    target_country: c.target_country ?? '',
    target_language: c.target_language ?? '',
    target_role: c.target_role ?? '',
    prospect_count: String(c.prospect_count ?? 120),
    calendar_link: c.calendar_link ?? '',
    timezone: c.timezone ?? '',
    available_hours: c.available_hours ?? '',
    tone: c.tone ?? '',
    allowed_channels: Array.isArray(c.allowed_channels) ? [...c.allowed_channels] : [...DEFAULT_ALLOWED_CHANNELS],
    autopilot_status: c.autopilot_status ?? 'off',
    sender_name: c.sender_name ?? '',
    sender_email: c.sender_email ?? '',
    ai_context: c.ai_context ?? '',
    followup_delay_days: c.followup_delay_days != null ? String(c.followup_delay_days) : '',
    max_auto_followups: c.max_auto_followups != null ? String(c.max_auto_followups) : '',
    inbound_reply_mode: c.inbound_reply_mode ?? 'draft_only',
    inbound_reply_delay_minutes: String(c.inbound_reply_delay_minutes ?? 2),
  }
}

/**
 * sellers: solo para asignar internamente el primer SDR/AE (simulación de “usuario actual”).
 * No se muestra en UI.
 */
export function CampaignFormModal({
  open,
  onClose,
  companyId,
  products,
  sellers,
  onCreated,
  mode = 'create',
  campaignId = null,
  initialCampaign = null,
  onSaved,
}) {
  const isEdit = mode === 'edit'
  const [form, setForm] = useState(defaultForm())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const simulatedSellerId = useMemo(() => {
    const first = sellers?.find((u) => u.role === 'seller')
    return first?.id ?? null
  }, [sellers])

  useEffect(() => {
    if (!open) {
      return
    }
    setError(null)
    if (isEdit && initialCampaign) {
      setForm(formFromCampaign(initialCampaign))
    } else {
      setForm(defaultForm())
    }
  }, [open, isEdit, initialCampaign])

  const productDatalistId = 'dl-campaign-products'
  const prospectParsed = Number(form.prospect_count)

  function resolveProductId() {
    const q = form.productQuery.trim().toLowerCase()
    if (!q) {
      return null
    }
    const exact = products.find((p) => p.name.trim().toLowerCase() === q)
    if (exact) {
      return exact.id
    }
    const partial = products.find((p) => p.name.toLowerCase().includes(q))
    return partial?.id ?? null
  }

  async function handleSubmit(ev) {
    ev.preventDefault()
    if (!companyId) {
      return
    }
    if (isEdit && (!campaignId || campaignId < 1)) {
      setError('Campaña inválida para editar.')
      return
    }

    const icpOk = icpHasMinimumSignal({
      target_company_size: form.target_company_size,
      target_industry: form.target_industry,
      target_country: form.target_country,
      target_language: form.target_language,
      target_role: form.target_role,
    })
    if (!icpOk) {
      setError(ICP_MISSING_MESSAGE)
      return
    }

    const sellerIdForCreate = simulatedSellerId
    const sellerIdParsed = Number(form.seller_id)
    if (isEdit) {
      if (!Number.isFinite(sellerIdParsed) || sellerIdParsed < 1) {
        setError('Seleccioná el SDR/AE asignado a la campaña.')
        return
      }
    } else if (!sellerIdForCreate) {
      setError(
        'No hay SDR/AE en la empresa para asignar la campaña (simulación). Agregá un usuario con rol seller.',
      )
      return
    }

    const productId = resolveProductId()
    if (!productId) {
      setError(
        'Seleccioná o escribí el nombre exacto de un producto activo de la lista sugerida.',
      )
      return
    }

    if (!Number.isFinite(prospectParsed) || prospectParsed < 1) {
      setError('Indicá una cantidad válida de prospectos a contactar (mínimo 1).')
      return
    }

    let followup_delay_days = null
    if (form.followup_delay_days.trim()) {
      const n = Number(form.followup_delay_days)
      if (!Number.isFinite(n) || n < 1 || n > 90) {
        setError('Días entre follow-ups: entre 1 y 90, o vacío para default del servidor.')
        return
      }
      followup_delay_days = n
    }

    let max_auto_followups = null
    if (form.max_auto_followups.trim()) {
      const n = Number(form.max_auto_followups)
      if (!Number.isFinite(n) || n < 1 || n > 50) {
        setError('Máx. follow-ups automáticos: entre 1 y 50, o vacío para default del servidor.')
        return
      }
      max_auto_followups = n
    }

    const delayMin = Number(form.inbound_reply_delay_minutes)
    if (![1, 2, 5].includes(delayMin)) {
      setError('Responder después de: elige 1, 2 o 5 minutos.')
      return
    }

    const common = {
      name: form.name.trim(),
      target_company_size: toApiIcp(form.target_company_size),
      target_industry: toApiIcp(form.target_industry),
      target_country: toApiIcp(form.target_country),
      target_language: toApiIcp(form.target_language),
      target_role: toApiIcp(form.target_role),
      prospect_count: prospectParsed,
      calendar_link: form.calendar_link.trim(),
      timezone: form.timezone.trim(),
      available_hours: form.available_hours.trim(),
      tone: form.tone.trim(),
      allowed_channels: form.allowed_channels,
      sender_name: form.sender_name.trim() || null,
      sender_email: form.sender_email.trim() || null,
      ai_context: form.ai_context.trim() || null,
      followup_delay_days,
      max_auto_followups,
      inbound_reply_mode: form.inbound_reply_mode,
      inbound_reply_delay_minutes: delayMin,
    }

    setSaving(true)
    setError(null)
    try {
      if (isEdit) {
        await updateCampaign(campaignId, {
          ...common,
          seller_id: sellerIdParsed,
          product_id: productId,
          autopilot_status: form.autopilot_status,
        })
        onSaved?.()
      } else {
        await createCampaign(companyId, {
          ...common,
          seller_id: sellerIdForCreate,
          product_id: productId,
        })
        onCreated?.()
      }
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (!open) {
    return null
  }

  const btnPrimary =
    'rounded-lg bg-nx-brand px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-50'
  const btnGhost =
    'rounded-lg border border-[#e5e7eb] bg-white px-4 py-2.5 text-sm font-medium text-[#374151] hover:bg-[#f8fafc]'

  return (
    <Modal
      title={isEdit ? 'Editar campaña' : 'Nueva campaña'}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={btnGhost} onClick={onClose}>
            Cancelar
          </button>
          <button
            type="submit"
            form="campaign-form"
            disabled={
              saving || !companyId || (isEdit && (!campaignId || !initialCampaign))
            }
            className={btnPrimary}
          >
            {saving ? 'Guardando…' : isEdit ? 'Guardar cambios' : 'Guardar campaña'}
          </button>
        </>
      }
    >
      <form id="campaign-form" onSubmit={handleSubmit} className="flex min-h-0 flex-col">
        <div className="space-y-6 pr-1">
          <AlertBanner message={error} onDismiss={() => setError(null)} />

          <section className={sectionBox}>
            <h3 className={sectionTitle}>Datos de campaña</h3>
            <div>
              <label htmlFor="camp-name" className="text-xs font-medium text-[#374151]">
                Nombre de campaña
              </label>
              <input
                id="camp-name"
                required
                className={inputClass}
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Ej. Outbound LATAM Q2"
              />
            </div>
            <div>
              <label htmlFor="camp-product" className="text-xs font-medium text-[#374151]">
                Producto a vender
              </label>
              <input
                id="camp-product"
                required
                className={inputClass}
                value={form.productQuery}
                onChange={(e) => setForm((f) => ({ ...f, productQuery: e.target.value }))}
                list={productDatalistId}
                placeholder="Escribí o elegí de sugerencias"
                autoComplete="off"
              />
              <datalist id={productDatalistId}>
                {products.map((p) => (
                  <option key={p.id} value={p.name} />
                ))}
              </datalist>
              <p className="mt-1 text-[11px] text-[#9ca3af]">
                Podés escribir libremente; si coincide con un producto activo, se usará al guardar.
              </p>
            </div>
            {isEdit ? (
              <div>
                <label htmlFor="camp-seller" className="text-xs font-medium text-[#374151]">
                  SDR/AE asignado
                </label>
                <select
                  id="camp-seller"
                  required
                  className={inputClass}
                  value={form.seller_id}
                  onChange={(e) => setForm((f) => ({ ...f, seller_id: e.target.value }))}
                >
                  <option value="">Elegir…</option>
                  {(sellers ?? []).map((u) => (
                    <option key={u.id} value={String(u.id)}>
                      {u.name || `Usuario ${u.id}`}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
          </section>

          <section className={sectionBox}>
            <h3 className={sectionTitle}>ICP objetivo</h3>
            <p className="mb-2 text-[11px] text-[#9ca3af]">
              Completá al menos un criterio; en el detalle de la campaña verás el bloque <strong>ICP objetivo</strong>{' '}
              y lo podés ajustar cuando quieras.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <DatalistInput
                id="icp-size"
                listId="dl-company-size"
                label="Tamaño de empresa objetivo"
                value={form.target_company_size}
                onChange={(v) => setForm((f) => ({ ...f, target_company_size: v }))}
                suggestions={SUGGEST_COMPANY_SIZE}
              />
              <DatalistInput
                id="icp-industry"
                listId="dl-industry"
                label="Industria / sector"
                value={form.target_industry}
                onChange={(v) => setForm((f) => ({ ...f, target_industry: v }))}
                suggestions={SUGGEST_INDUSTRY}
              />
              <DatalistInput
                id="icp-country"
                listId="dl-country"
                label="País"
                value={form.target_country}
                onChange={(v) => setForm((f) => ({ ...f, target_country: v }))}
                suggestions={SUGGEST_COUNTRY}
              />
              <DatalistInput
                id="icp-lang"
                listId="dl-language"
                label="Idioma"
                value={form.target_language}
                onChange={(v) => setForm((f) => ({ ...f, target_language: v }))}
                suggestions={SUGGEST_LANGUAGE}
              />
              <div className="sm:col-span-2">
                <DatalistInput
                  id="icp-role"
                  listId="dl-role"
                  label="Rol objetivo"
                  value={form.target_role}
                  onChange={(v) => setForm((f) => ({ ...f, target_role: v }))}
                  suggestions={SUGGEST_ROLE}
                />
              </div>
            </div>
            <p className="text-[11px] leading-relaxed text-[#9ca3af]">
              Al menos un campo del ICP debe tener criterio (no puede quedar todo vacío o solo “No
              importante”).
            </p>
          </section>

          <section className={sectionBox}>
            <h3 className={sectionTitle}>Configuración de contacto</h3>
            <div>
              <label htmlFor="camp-cal" className="text-xs font-medium text-[#374151]">
                Link de Calendar
              </label>
              <input
                id="camp-cal"
                required
                type="text"
                className={inputClass}
                value={form.calendar_link}
                onChange={(e) =>
                  setForm((f) => ({ ...f, calendar_link: e.target.value }))
                }
                placeholder="https://…"
              />
            </div>
            <DatalistInput
              id="camp-tz"
              listId="dl-tz"
              label="Zona horaria"
              value={form.timezone}
              onChange={(v) => setForm((f) => ({ ...f, timezone: v }))}
              suggestions={SUGGEST_TIMEZONE}
              required
            />
            <DatalistInput
              id="camp-hours"
              listId="dl-hours"
              label="Horarios disponibles"
              value={form.available_hours}
              onChange={(v) => setForm((f) => ({ ...f, available_hours: v }))}
              suggestions={SUGGEST_AVAILABLE_HOURS}
              required
            />
            <DatalistInput
              id="camp-tone"
              listId="dl-tone"
              label="Tono de comunicación"
              value={form.tone}
              onChange={(v) => setForm((f) => ({ ...f, tone: v }))}
              suggestions={SUGGEST_TONE}
              required
            />
            <CampaignChannelsField
              value={form.allowed_channels}
              onChange={(channels) =>
                setForm((f) => ({ ...f, allowed_channels: channels }))
              }
            />
            <div>
              <label htmlFor="camp-prospects" className="text-xs font-medium text-[#374151]">
                Prospectos a contactar
              </label>
              <input
                id="camp-prospects"
                required
                inputMode="numeric"
                min={1}
                className={inputClass}
                value={form.prospect_count}
                onChange={(e) =>
                  setForm((f) => ({ ...f, prospect_count: e.target.value }))
                }
              />
            </div>
          </section>

          <section className={sectionBox}>
            <h3 className={sectionTitle}>Remitente, IA y seguimiento</h3>
            <p className="text-[11px] text-[#9ca3af]">
              El link de calendario y el contexto de IA impactan mensajes y sugerencias futuras sin borrar
              prospectos ni timeline.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="camp-sender-name" className="text-xs font-medium text-[#374151]">
                  Nombre remitente (firma / tono)
                </label>
                <input
                  id="camp-sender-name"
                  className={inputClass}
                  value={form.sender_name}
                  onChange={(e) => setForm((f) => ({ ...f, sender_name: e.target.value }))}
                  placeholder="Opcional"
                  autoComplete="off"
                />
              </div>
              <div>
                <label htmlFor="camp-sender-email" className="text-xs font-medium text-[#374151]">
                  Email remitente
                </label>
                <input
                  id="camp-sender-email"
                  type="email"
                  className={inputClass}
                  value={form.sender_email}
                  onChange={(e) => setForm((f) => ({ ...f, sender_email: e.target.value }))}
                  placeholder="Opcional"
                  autoComplete="off"
                />
              </div>
            </div>
            <div>
              <label htmlFor="camp-ai-ctx" className="text-xs font-medium text-[#374151]">
                Contexto / instrucciones IA (esta campaña)
              </label>
              <textarea
                id="camp-ai-ctx"
                rows={4}
                className={inputClass}
                value={form.ai_context}
                onChange={(e) => setForm((f) => ({ ...f, ai_context: e.target.value }))}
                placeholder="Mensajes clave, objeciones, límites, estilo… Se suma a las instrucciones globales de la empresa."
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="camp-fu-days" className="text-xs font-medium text-[#374151]">
                  Días hasta próximo follow-up automático
                </label>
                <input
                  id="camp-fu-days"
                  inputMode="numeric"
                  className={inputClass}
                  value={form.followup_delay_days}
                  onChange={(e) => setForm((f) => ({ ...f, followup_delay_days: e.target.value }))}
                  placeholder="Vacío = default servidor"
                />
              </div>
              <div>
                <label htmlFor="camp-fu-max" className="text-xs font-medium text-[#374151]">
                  Máx. follow-ups automáticos por prospecto
                </label>
                <input
                  id="camp-fu-max"
                  inputMode="numeric"
                  className={inputClass}
                  value={form.max_auto_followups}
                  onChange={(e) => setForm((f) => ({ ...f, max_auto_followups: e.target.value }))}
                  placeholder="Vacío = default servidor"
                />
              </div>
            </div>
            <div className="rounded-lg border border-[#e5e7eb] bg-white p-3">
              <p className="text-xs font-semibold text-[#374151]">Modo de respuesta (inbound Gmail)</p>
              <p className="mt-1 text-[11px] text-[#9ca3af]">
                Cuando un prospecto responde por email, Nexus clasifica y responde solo. Borrador automático es el
                modo seguro; envío automático manda el mail tras el delay configurado.
              </p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="camp-inbound-mode" className="text-xs font-medium text-[#374151]">
                    Modo de respuesta
                  </label>
                  <select
                    id="camp-inbound-mode"
                    className={inputClass}
                    value={form.inbound_reply_mode}
                    onChange={(e) => setForm((f) => ({ ...f, inbound_reply_mode: e.target.value }))}
                  >
                    <option value="draft_only">Borrador automático</option>
                    <option value="auto_send">Envío automático</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="camp-inbound-delay" className="text-xs font-medium text-[#374151]">
                    Responder después de
                  </label>
                  <select
                    id="camp-inbound-delay"
                    className={inputClass}
                    value={form.inbound_reply_delay_minutes}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, inbound_reply_delay_minutes: e.target.value }))
                    }
                    disabled={form.inbound_reply_mode !== 'auto_send'}
                  >
                    <option value="1">1 minuto</option>
                    <option value="2">2 minutos</option>
                    <option value="5">5 minutos</option>
                  </select>
                </div>
              </div>
            </div>
            {isEdit ? (
              <div>
                <label htmlFor="camp-autopilot" className="text-xs font-medium text-[#374151]">
                  Modo autopilot (simulado)
                </label>
                <select
                  id="camp-autopilot"
                  className={inputClass}
                  value={form.autopilot_status}
                  onChange={(e) => setForm((f) => ({ ...f, autopilot_status: e.target.value }))}
                >
                  <option value="off">Manual — autopilot apagado</option>
                  <option value="running">Automático activo</option>
                  <option value="paused">Automático en pausa</option>
                  <option value="completed">Ciclo autopilot completado</option>
                </select>
                <p className="mt-1 text-[11px] text-[#9ca3af]">
                  No detiene la secuencia de prospectos salvo que uses el flujo de outreach; solo afecta el autopilot
                  por campaña.
                </p>
              </div>
            ) : null}
          </section>
        </div>
      </form>
    </Modal>
  )
}
