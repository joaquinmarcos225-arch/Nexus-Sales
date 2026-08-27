import { useEffect, useMemo, useRef, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { AlertBanner } from '../AlertBanner.jsx'
import { createCampaign, updateCampaign } from '../../utils/api.js'
import { useAuth } from '../../context/AuthContext.jsx'
import {
  SUGGEST_AVAILABLE_HOURS,
  SUGGEST_AREA,
  SUGGEST_B2C_PROFILE,
  SUGGEST_B2C_SITUATION,
  SUGGEST_COMPANY_SIZE,
  SUGGEST_REGION,
  SUGGEST_INDUSTRY,
  SUGGEST_INTERESTS,
  SUGGEST_LANGUAGE,
  SUGGEST_ROLE,
  SUGGEST_TONE,
} from '../../data/campaignFormSuggestions.js'
import {
  ICP_MISSING_MESSAGE,
  icpHasMinimumSignal,
  icpLooksEmpty,
} from '../../utils/icp.js'
import { DEFAULT_ALLOWED_CHANNELS } from '../../utils/campaignChannels.js'
import { isCampaignAssignableUser } from '../../utils/campaignUsers.js'
import { ROLES, normalizeRole } from '../../data/navigation.js'
import { CampaignChannelsField } from './CampaignChannelsField.jsx'
import { SequenceTemplatePicker } from './SequenceTemplatePicker.jsx'
import { TimezoneSelect } from './TimezoneSelect.jsx'
import { SuggestSelect } from './SuggestSelect.jsx'
import { resolveTimezoneQuery, buildTimezoneOptions } from '../../utils/timezones.js'
import { formatContactCredits } from '../../utils/format.js'
import { notifyCreditsChanged, useMyCredits } from '../../hooks/useMyCredits.js'

const inputClass =
  'mt-1 w-full rounded-md border border-nx-border bg-white px-2.5 py-1.5 text-sm text-nx-ink shadow-none placeholder:text-nx-subtle focus:border-nx-brand/50 focus:outline-none focus:ring-1 focus:ring-nx-brand/20'

const inputClassError =
  'mt-1 w-full rounded-md border border-red-300 bg-white px-2.5 py-1.5 text-sm text-nx-ink shadow-none placeholder:text-nx-subtle focus:border-red-400 focus:outline-none focus:ring-1 focus:ring-red-400/20'

const sectionTitle =
  'text-xs font-bold uppercase tracking-[0.1em] text-nx-ink'

const sectionBox =
  'rounded-xl border-2 border-nx-brand/55 bg-nx-card-muted/90 p-4 shadow-sm space-y-3'

function currentUserId(user) {
  const raw = user?.user_id ?? user?.id
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? n : null
}

function toApiIcp(value) {
  const v = String(value ?? '').trim()
  if (icpLooksEmpty(v)) {
    return null
  }
  return v
}

const defaultForm = () => ({
  name: '',
  productQuery: '',
  seller_id: '',
  outreach_mode: 'b2b',
  target_company_size: '',
  target_industry: '',
  target_country: '',
  target_language: '',
  target_role: '',
  target_area: '',
  target_interests: '',
  prospect_count: '30',
  timezone: '',
  available_hours: '',
  tone: '',
  allowed_channels: [...DEFAULT_ALLOWED_CHANNELS],
  sequence_plan: null,
  autopilot_status: 'off',
  post_sequence_followup_enabled: 'yes',
  followup_delay_days: '',
})

function formFromCampaign(c) {
  return {
    name: c.name ?? '',
    productQuery: c.product_name ?? '',
    seller_id: c.seller_id != null ? String(c.seller_id) : '',
    outreach_mode: c.outreach_mode === 'b2c' ? 'b2c' : 'b2b',
    target_company_size: c.target_company_size ?? '',
    target_industry: c.target_industry ?? '',
    target_country: c.target_country ?? '',
    target_language: c.target_language ?? '',
    target_role: c.target_role ?? '',
    target_area: c.target_area ?? '',
    target_interests: c.target_interests ?? '',
    prospect_count: String(c.prospect_count ?? 120),
    timezone: c.timezone ?? '',
    available_hours: c.available_hours ?? '',
    tone: c.tone ?? '',
    allowed_channels: Array.isArray(c.allowed_channels) ? [...c.allowed_channels] : [...DEFAULT_ALLOWED_CHANNELS],
    autopilot_status: c.autopilot_status ?? 'off',
    post_sequence_followup_enabled:
      c.post_sequence_followup_enabled === false ? 'no' : 'yes',
    followup_delay_days: c.followup_delay_days != null ? String(c.followup_delay_days) : '',
    sequence_plan: c.sequence_plan ?? null,
  }
}

/**
 * La campaña se asigna al usuario logueado (SDR/Manager). Créditos de equipo se gestionan en /creditos.
 */
export function CampaignFormModal({
  open,
  onClose,
  companyId,
  products,
  onCreated,
  mode = 'create',
  campaignId = null,
  initialCampaign = null,
  onSaved,
  sellerCreditAvailable = null,
  prospectsImported = 0,
}) {
  const isEdit = mode === 'edit'
  const { user } = useAuth()
  const isSdrUser = normalizeRole(user?.role) === ROLES.sdr
  const { available: myCreditsAvailable } = useMyCredits()
  const [form, setForm] = useState(defaultForm())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const errorRef = useRef(null)

  useEffect(() => {
    if (error && errorRef.current) {
      errorRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [error])

  const currentSellerId = useMemo(() => {
    if (isEdit && initialCampaign?.seller_id) {
      return Number(initialCampaign.seller_id)
    }
    if (user && isCampaignAssignableUser(user)) {
      return currentUserId(user)
    }
    return null
  }, [isEdit, initialCampaign?.seller_id, user])

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

  const creditCap = useMemo(() => {
    const base =
      sellerCreditAvailable != null ? Number(sellerCreditAvailable) : Number(myCreditsAvailable) || 0
    const reserved =
      isEdit && initialCampaign?.prospect_count
        ? Number(initialCampaign.prospect_count) || 0
        : 0
    return Math.max(0, base + reserved)
  }, [sellerCreditAvailable, myCreditsAvailable, isEdit, initialCampaign?.prospect_count])

  const prospectExceedsCredits = useMemo(() => {
    if (!Number.isFinite(prospectParsed) || prospectParsed < 1) return false
    return prospectParsed > creditCap
  }, [creditCap, prospectParsed])

  const minProspections = useMemo(() => {
    if (!isEdit) return 1
    return Math.max(1, Number(prospectsImported) || 0)
  }, [isEdit, prospectsImported])

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

  const selectedProduct = useMemo(() => {
    const id = (() => {
      const q = form.productQuery.trim().toLowerCase()
      if (!q) return null
      const exact = products.find((p) => p.name.trim().toLowerCase() === q)
      if (exact) return exact
      return products.find((p) => p.name.toLowerCase().includes(q)) ?? null
    })()
    return id
  }, [form.productQuery, products])

  const productScope = selectedProduct?.market_scope || 'b2b'
  const productAllowsBoth = productScope === 'both'
  const effectiveOutreachMode = useMemo(() => {
    if (productScope === 'b2c') return 'b2c'
    if (productScope === 'b2b') return 'b2b'
    return form.outreach_mode === 'b2c' ? 'b2c' : 'b2b'
  }, [productScope, form.outreach_mode])

  const isB2cCampaign = effectiveOutreachMode === 'b2c'

  useEffect(() => {
    if (!open || !selectedProduct) return
    if (productScope === 'b2c' && form.outreach_mode !== 'b2c') {
      setForm((f) => ({ ...f, outreach_mode: 'b2c' }))
    } else if (productScope === 'b2b' && form.outreach_mode !== 'b2b') {
      setForm((f) => ({ ...f, outreach_mode: 'b2b' }))
    }
  }, [open, selectedProduct, productScope, form.outreach_mode])

  async function handleSubmit(ev) {
    ev.preventDefault()
    if (!companyId) {
      setError('No hay empresa seleccionada.')
      return
    }
    if (isEdit && (!campaignId || campaignId < 1)) {
      setError('Campaña inválida para editar.')
      return
    }

    if (!form.name.trim()) {
      setError('Indicá un nombre para la campaña.')
      return
    }

    let timezone = form.timezone.trim()
    if (!timezone) {
      timezone = resolveTimezoneQuery(
        document.getElementById('camp-tz')?.value,
        buildTimezoneOptions(),
      )
    }
    if (!timezone) {
      setError('Elegí una región de la lista (ej. LATAM, Brasil, Argentina).')
      return
    }

    if (!form.available_hours.trim()) {
      setError('Indicá los horarios disponibles.')
      return
    }

    if (!form.tone.trim()) {
      setError('Indicá el tono de comunicación.')
      return
    }

    const icpOk = isB2cCampaign
      ? !icpLooksEmpty(form.target_country) &&
        (!icpLooksEmpty(form.target_role) || !icpLooksEmpty(form.target_interests))
      : icpHasMinimumSignal({
          target_company_size: form.target_company_size,
          target_industry: form.target_industry,
          target_country: form.target_country,
          target_language: form.target_language,
          target_role: form.target_role,
          target_area: form.target_area,
        })
    if (!icpOk) {
      setError(
        isB2cCampaign
          ? 'ICP B2C: completá región y quién buscamos o keywords (LinkedIn).'
          : ICP_MISSING_MESSAGE,
      )
      return
    }

    if (!currentSellerId || !Number.isFinite(currentSellerId) || currentSellerId < 1) {
      setError(
        'No se pudo identificar tu usuario para crear la campaña. Cerrá sesión y volvé a entrar, o pedí créditos en Créditos.',
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

    if (!Number.isFinite(prospectParsed) || prospectParsed < minProspections) {
      setError(
        minProspections > 1
          ? `Indicá al menos ${minProspections} prospecciones (contactos ya importados en esta campaña).`
          : 'Indicá una cantidad válida de prospecciones (mínimo 1).',
      )
      return
    }

    if (prospectExceedsCredits) {
      setError(
        `Solo tenés ${formatContactCredits(creditCap)} disponibles. No podés planificar ${prospectParsed} prospecciones.`,
      )
      return
    }

    let followup_delay_days = null
    const postSequenceFollowupEnabled = form.post_sequence_followup_enabled === 'yes'

    if (postSequenceFollowupEnabled && form.followup_delay_days.trim()) {
      const n = Number(form.followup_delay_days)
      if (!Number.isFinite(n) || n < 1 || n > 365) {
        setError('Cuántos días hasta el follow-up: entre 1 y 365, o vacío para 30 días (default).')
        return
      }
      followup_delay_days = n
    }

    const sequencePlan = form.sequence_plan
      ? {
          ...form.sequence_plan,
          follow_up: {
            enabled: postSequenceFollowupEnabled,
            channel: postSequenceFollowupEnabled
              ? form.sequence_plan.follow_up?.channel || 'auto'
              : 'auto',
          },
        }
      : null

    const common = {
      name: form.name.trim(),
      outreach_mode: effectiveOutreachMode,
      target_company_size: isB2cCampaign ? null : toApiIcp(form.target_company_size),
      target_industry: isB2cCampaign ? null : toApiIcp(form.target_industry),
      target_country: toApiIcp(form.target_country),
      target_language: toApiIcp(form.target_language),
      target_role: toApiIcp(form.target_role),
      target_area: toApiIcp(form.target_area),
      target_interests: toApiIcp(form.target_interests),
      prospect_count: prospectParsed,
      timezone,
      available_hours: form.available_hours.trim(),
      tone: form.tone.trim(),
      allowed_channels: form.allowed_channels,
      post_sequence_followup_enabled: postSequenceFollowupEnabled,
      followup_delay_days: postSequenceFollowupEnabled ? followup_delay_days : null,
      max_auto_followups: postSequenceFollowupEnabled ? 1 : null,
      outreach_email_mode: 'auto_send',
      inbound_reply_mode: 'auto_send',
      sequence_plan: sequencePlan,
    }

    setSaving(true)
    setError(null)
    try {
      if (isEdit) {
        await updateCampaign(campaignId, {
          ...common,
          seller_id: currentSellerId,
          product_id: productId,
          autopilot_status: form.autopilot_status,
        })
        onSaved?.()
      } else {
        await createCampaign(companyId, {
          ...common,
          // Calendar: la IA usa la conexión Google; no se pide link manual.
          calendar_link: '',
          seller_id: currentSellerId,
          product_id: productId,
        })
        onCreated?.()
      }
      notifyCreditsChanged()
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

  const btnPrimary = 'nx-btn nx-btn-primary px-4 py-2.5 text-sm'
  const btnGhost =
    'rounded-lg border border-nx-border bg-white px-4 py-2.5 text-sm font-medium text-nx-ink hover:bg-nx-card-muted'

  return (
    <Modal
      title={isEdit ? 'Editar campaña' : 'Nueva campaña'}
      onClose={onClose}
      footer={
        <div className="flex w-full flex-col gap-3">
          {error ? (
            <AlertBanner message={error} onDismiss={() => setError(null)} />
          ) : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button type="button" className={btnGhost} onClick={onClose}>
              Cancelar
            </button>
            <button
              type="button"
              disabled={
                saving ||
                !companyId ||
                prospectExceedsCredits ||
                (isEdit && (!campaignId || !initialCampaign))
              }
              className={btnPrimary}
              onClick={(e) => handleSubmit(e)}
            >
              {saving ? 'Guardando…' : isEdit ? 'Guardar cambios' : 'Guardar campaña'}
            </button>
          </div>
        </div>
      }
    >
      <form id="campaign-form" noValidate onSubmit={handleSubmit} className="flex min-h-0 flex-col">
        <div className="space-y-6 pr-1">
          <div ref={errorRef}>
            <AlertBanner message={error} onDismiss={() => setError(null)} />
          </div>

          <section className={sectionBox}>
            <h3 className={sectionTitle}>Datos de campaña</h3>
            {isSdrUser && !isEdit ? (
              <p className="rounded-lg border border-nx-brand/25 bg-nx-brand/5 px-3 py-2 text-[11px] leading-relaxed text-nx-ink">
                Modo SDR: elegí producto, ICP básico y la plantilla{' '}
                <strong>LinkedIn → Email → WhatsApp</strong>. Nexus busca contactos al iniciar la secuencia.
              </p>
            ) : null}
            <div>
              <label htmlFor="camp-name" className="text-xs font-medium text-nx-ink">
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
              <label htmlFor="camp-product" className="text-xs font-medium text-nx-ink">
                Producto/servicio a vender
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
              <p className="mt-1 text-[11px] text-nx-subtle">
                Podés escribir libremente; si coincide con un producto activo, se usará al guardar.
              </p>
            </div>
            {selectedProduct ? (
              <div>
                <p className="text-xs font-medium text-nx-ink">Tipo de campaña</p>
                {productAllowsBoth ? (
                  <>
                    <p className="mt-0.5 text-[11px] text-nx-subtle">
                      Este producto admite B2B y B2C. Elegí el modo de esta campaña (no se mezclan).
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {[
                        { value: 'b2b', label: 'B2B', hint: 'Empresas → contactos' },
                        { value: 'b2c', label: 'B2C', hint: 'Personas directas' },
                      ].map((opt) => {
                        const active = effectiveOutreachMode === opt.value
                        return (
                          <button
                            key={opt.value}
                            type="button"
                            className={[
                              'rounded-lg border px-3 py-2 text-left transition',
                              active
                                ? 'border-nx-brand bg-nx-brand/10 text-nx-ink ring-1 ring-nx-brand/30'
                                : 'border-nx-border bg-white text-nx-muted hover:bg-nx-card-muted',
                            ].join(' ')}
                            onClick={() => setForm((f) => ({ ...f, outreach_mode: opt.value }))}
                          >
                            <span className="block text-sm font-semibold">{opt.label}</span>
                            <span className="block text-[10px] opacity-80">{opt.hint}</span>
                          </button>
                        )
                      })}
                    </div>
                  </>
                ) : (
                  <p className="mt-1 text-[11px] text-nx-subtle">
                    Modo fijo del producto:{' '}
                    <strong className="text-nx-ink">
                      {effectiveOutreachMode === 'b2c' ? 'B2C' : 'B2B'}
                    </strong>
                    . Misma secuencia; cambia el perfil y la búsqueda.
                  </p>
                )}
              </div>
            ) : null}
          </section>

          <section className={sectionBox}>
            <h3 className={sectionTitle}>
              {isB2cCampaign ? 'Perfil de persona (B2C)' : 'Perfil objetivo'}
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <SuggestSelect
                id="icp-role"
                label={isB2cCampaign ? 'Quién buscamos' : 'Rol objetivo'}
                value={form.target_role}
                onChange={(v) => setForm((f) => ({ ...f, target_role: v }))}
                suggestions={isB2cCampaign ? SUGGEST_B2C_PROFILE : SUGGEST_ROLE}
                hint="Podés escribir cualquier cargo. Eso es lo que Nexus busca; la lista es atajo."
                placeholder="Ej. Gerente de planta, Director médico…"
              />
              <div>
                <SuggestSelect
                  id="icp-area"
                  label={isB2cCampaign ? 'Situación / momento' : 'Área objetivo'}
                  value={form.target_area}
                  onChange={(v) => setForm((f) => ({ ...f, target_area: v }))}
                  suggestions={isB2cCampaign ? SUGGEST_B2C_SITUATION : SUGGEST_AREA}
                />
                {isB2cCampaign ? (
                  <p className="mt-1 text-[11px] text-nx-subtle">
                    Solo para redactar mensajes. No filtra leads en Prospeo.
                  </p>
                ) : null}
              </div>
            </div>
            {isB2cCampaign ? (
              <p className="mt-2 text-[11px] text-nx-subtle">
                Tipo de persona (no un cargo B2B). Ej.: comprador de vivienda, inversor particular,
                freelancer.
              </p>
            ) : null}
          </section>

          <section className={sectionBox}>
            <h3 className={sectionTitle}>
              {isB2cCampaign ? 'ICP B2C — criterios' : 'ICP — criterios adicionales'}
            </h3>
            <p className="mb-2 text-[11px] text-nx-subtle">
              {isB2cCampaign
                ? 'Completá región + quién buscamos o keywords. Nexus busca personas (Prospeo), no empresas.'
                : 'Completá al menos un criterio (incluido rol o área arriba); en el detalle verás el bloque ICP objetivo.'}
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {isB2cCampaign ? (
                <>
                  <div>
                    <SuggestSelect
                      id="icp-interests"
                      label="Señales / keywords (LinkedIn)"
                      value={form.target_interests}
                      onChange={(v) => setForm((f) => ({ ...f, target_interests: v }))}
                      suggestions={SUGGEST_INTERESTS}
                    />
                    <p className="mt-1 text-[11px] text-nx-subtle">
                      Palabras que suelen aparecer en perfil o titular. Prospeo las usa para buscar.
                    </p>
                  </div>
                  <div>
                    <SuggestSelect
                      id="icp-region"
                      label="Región / país"
                      value={form.target_country}
                      onChange={(v) => setForm((f) => ({ ...f, target_country: v }))}
                      suggestions={SUGGEST_REGION}
                    />
                    <p className="mt-1 text-[11px] text-nx-subtle">
                      LATAM - Brasil = Hispanoamérica sin Brasil · LATAM + Brasil = toda Latinoamérica
                    </p>
                  </div>
                  <div>
                    <SuggestSelect
                      id="icp-lang"
                      label="Idioma"
                      value={form.target_language}
                      onChange={(v) => setForm((f) => ({ ...f, target_language: v }))}
                      suggestions={SUGGEST_LANGUAGE}
                    />
                    <p className="mt-1 text-[11px] text-nx-subtle">
                      Se usa en los mensajes. No filtra la búsqueda en Prospeo.
                    </p>
                  </div>
                </>
              ) : (
                <>
                  <SuggestSelect
                    id="icp-size"
                    label="Tamaño de empresa objetivo"
                    value={form.target_company_size}
                    onChange={(v) => setForm((f) => ({ ...f, target_company_size: v }))}
                    suggestions={SUGGEST_COMPANY_SIZE}
                  />
                  <SuggestSelect
                    id="icp-industry"
                    label="Industria / sector"
                    value={form.target_industry}
                    onChange={(v) => setForm((f) => ({ ...f, target_industry: v }))}
                    suggestions={SUGGEST_INDUSTRY}
                    hint="Podés escribir el sector exacto si no está en la lista."
                    placeholder="Ej. Clínicas, Agro, Construcción…"
                  />
                  <div>
                    <SuggestSelect
                      id="icp-region"
                      label="Región"
                      value={form.target_country}
                      onChange={(v) => setForm((f) => ({ ...f, target_country: v }))}
                      suggestions={SUGGEST_REGION}
                    />
                    <p className="mt-1 text-[11px] text-nx-subtle">
                      LATAM - Brasil = Hispanoamérica sin Brasil · LATAM + Brasil = toda Latinoamérica incluido Brasil
                    </p>
                  </div>
                  <SuggestSelect
                    id="icp-lang"
                    label="Idioma"
                    value={form.target_language}
                    onChange={(v) => setForm((f) => ({ ...f, target_language: v }))}
                    suggestions={SUGGEST_LANGUAGE}
                  />
                </>
              )}
            </div>
            <p className="text-[11px] leading-relaxed text-nx-subtle">
              Al menos un campo del ICP debe tener criterio (no puede quedar todo vacío o solo “No
              importante”). Lo que escribas (rol, industria, etc.) es lo que se busca; la lista es
              ayuda, no un menú cerrado.
            </p>
            <div className="mt-4 space-y-3 border-t border-nx-border pt-4">
              <div>
                <p className="text-xs font-medium text-nx-ink">
                  ¿Generar follow-up después de la secuencia?
                </p>
                <p className="mt-0.5 text-[11px] text-nx-subtle">
                  Tras la secuencia inicial, Nexus puede recontactar automáticamente una vez si el
                  prospecto no respondió.
                </p>
                <div className="mt-2 flex flex-wrap gap-4">
                  <label className="flex cursor-pointer items-center gap-2 text-sm text-nx-ink">
                    <input
                      type="radio"
                      name="post-sequence-followup"
                      checked={form.post_sequence_followup_enabled === 'yes'}
                      onChange={() =>
                        setForm((f) => ({ ...f, post_sequence_followup_enabled: 'yes' }))
                      }
                    />
                    Sí
                  </label>
                  <label className="flex cursor-pointer items-center gap-2 text-sm text-nx-ink">
                    <input
                      type="radio"
                      name="post-sequence-followup"
                      checked={form.post_sequence_followup_enabled === 'no'}
                      onChange={() =>
                        setForm((f) => ({
                          ...f,
                          post_sequence_followup_enabled: 'no',
                          followup_delay_days: '',
                        }))
                      }
                    />
                    No
                  </label>
                </div>
              </div>
              {form.post_sequence_followup_enabled === 'yes' ? (
                <div>
                  <label htmlFor="camp-fu-days" className="text-xs font-medium text-nx-ink">
                    Cuántos días hasta el follow-up
                  </label>
                  <p className="mt-0.5 text-[11px] text-nx-subtle">
                    Tiempo después de terminar la secuencia inicial antes del primer recontacto automático.
                  </p>
                  <input
                    id="camp-fu-days"
                    inputMode="numeric"
                    className={inputClass}
                    value={form.followup_delay_days}
                    onChange={(e) => setForm((f) => ({ ...f, followup_delay_days: e.target.value }))}
                    placeholder="Vacío = 30 días (1 mes)"
                  />
                </div>
              ) : null}
            </div>
          </section>

          <section className={sectionBox}>
            <h3 className={sectionTitle}>Configuración de contacto</h3>
            <TimezoneSelect
              id="camp-tz"
              value={form.timezone}
              onChange={(v) => setForm((f) => ({ ...f, timezone: v }))}
              required
            />
            <SuggestSelect
              id="camp-hours"
              label="Horarios disponibles"
              value={form.available_hours}
              onChange={(v) => setForm((f) => ({ ...f, available_hours: v }))}
              suggestions={SUGGEST_AVAILABLE_HOURS}
              required
            />
            <SuggestSelect
              id="camp-tone"
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
              companyId={companyId}
              sellerId={currentSellerId}
            />
            <SequenceTemplatePicker
              companyId={companyId}
              value={form.sequence_plan}
              onChange={(plan) => setForm((f) => ({ ...f, sequence_plan: plan }))}
              allowedChannels={form.allowed_channels}
              campaignFollowupEnabled={form.post_sequence_followup_enabled === 'yes'}
            />
            <div>
              <label htmlFor="camp-prospects" className="text-xs font-medium text-nx-ink">
                Prospecciones (créditos)
              </label>
              <input
                id="camp-prospects"
                required
                inputMode="numeric"
                min={minProspections}
                max={creditCap > 0 ? creditCap : undefined}
                aria-invalid={prospectExceedsCredits}
                aria-describedby={prospectExceedsCredits ? 'camp-prospects-error' : undefined}
                className={prospectExceedsCredits ? inputClassError : inputClass}
                value={form.prospect_count}
                onChange={(e) =>
                  setForm((f) => ({ ...f, prospect_count: e.target.value }))
                }
              />
              {prospectExceedsCredits ? (
                <p id="camp-prospects-error" className="mt-1 text-xs font-medium text-red-600">
                  Superás tu saldo: tenés {formatContactCredits(creditCap)} disponibles. Bajá las
                  prospecciones o pedí más créditos a tu manager.
                </p>
              ) : (
                <p className="mt-1 text-[11px] text-nx-subtle">
                  1 crédito = 1 persona en secuencia completa (búsqueda → toques + follow-up
                  opcional). Al crear la campaña se comprometen esos créditos.
                  {` Máximo según tu saldo: ${formatContactCredits(creditCap)}.`}
                  {isEdit && minProspections > 1
                    ? ` Mínimo ${minProspections} (ya importados).`
                    : ''}
                </p>
              )}
            </div>
          </section>

          {isEdit ? (
          <section className={sectionBox}>
            <h3 className={sectionTitle}>Autopilot</h3>
              <div>
                <label htmlFor="camp-autopilot" className="text-xs font-medium text-nx-ink">
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
                <p className="mt-1 text-[11px] text-nx-subtle">
                  No detiene la secuencia de prospectos salvo que uses el flujo de outreach; solo afecta el autopilot
                  por campaña.
                </p>
              </div>
          </section>
          ) : null}
        </div>
      </form>
    </Modal>
  )
}
