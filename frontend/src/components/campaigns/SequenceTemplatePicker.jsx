import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createSequenceTemplate,
  deleteSequenceTemplate,
  fetchSequenceTemplates,
} from '../../utils/api.js'
import { CHANNEL_LABELS } from '../../utils/campaignChannels.js'
import { SEQUENCE_TOUCH_DAYS } from '../../utils/sequenceUi.js'

const CHANNELS = ['email', 'linkedin', 'whatsapp']
const FOLLOWUP_CHANNELS = ['auto', ...CHANNELS]
const MAX_TOUCHES = SEQUENCE_TOUCH_DAYS.length

/** Colores de marca de cada app. */
const CHANNEL_DOT = {
  email: 'bg-[#EA4335]',
  linkedin: 'bg-[#0A66C2]',
  whatsapp: 'bg-[#25D366]',
}

const CHANNEL_SELECTED = {
  email: 'border-[#EA4335] bg-[#EA4335]/10 text-nx-ink',
  linkedin: 'border-[#0A66C2] bg-[#0A66C2]/10 text-nx-ink',
  whatsapp: 'border-[#25D366] bg-[#25D366]/10 text-nx-ink',
}

function channelLabel(ch) {
  return CHANNEL_LABELS[String(ch || '').toLowerCase()] || ch
}

function followupLabel(ch) {
  if (ch === 'auto') return 'Automático (mejor disponible)'
  return channelLabel(ch)
}

function defaultDraftSteps(count = MAX_TOUCHES) {
  const n = Math.min(MAX_TOUCHES, Math.max(1, Number(count) || MAX_TOUCHES))
  const base = { 1: 'email', 4: 'linkedin', 7: 'whatsapp', 10: 'email', 13: 'linkedin', 16: 'whatsapp', 19: 'email' }
  return SEQUENCE_TOUCH_DAYS.slice(0, n).map((day) => ({ day, channel: base[day] || 'email' }))
}

function planFromTemplate(t, campaignFollowupEnabled = true) {
  return {
    template_id: t.template_id,
    template_name: t.name,
    mode: t.mode,
    steps: (t.steps || []).map((s) => ({ day: s.day, channel: s.channel })),
    follow_up: {
      enabled: Boolean(campaignFollowupEnabled),
      channel: t.follow_up?.channel || 'auto',
    },
  }
}

function successBadge(t) {
  if (t.template_id === 'nexus_3_li_email_wa') {
    return { text: 'Recomendada SDR', tone: 'good', sample: t.sample_size || 0 }
  }
  if (t.template_id === 'nexus_7') {
    return { text: 'Éxito: 36%', tone: 'good', sample: t.sample_size || 30 }
  }
  if (t.template_id === 'nexus_ia') {
    return { text: 'Éxito: 38%', tone: 'good', sample: t.sample_size || 30 }
  }
  if (t.success_rate == null || !t.sample_size) {
    return { text: 'Sin datos aún', tone: 'muted' }
  }
  const pct = Math.round(Number(t.success_rate) * 100)
  return { text: `Éxito: ${pct}%`, tone: pct >= 15 ? 'good' : 'ok', sample: t.sample_size }
}

function TemplateCard({ t, selected, onSelect, onDelete }) {
  const badge = successBadge(t)
  const isIa = t.mode === 'ia'
  return (
    <label
      className={[
        'block cursor-pointer rounded-xl border p-3 transition-colors',
        selected
          ? 'border-nx-brand bg-nx-brand/5'
          : 'border-nx-border bg-white hover:border-nx-border-strong',
      ].join(' ')}
    >
      <div className="flex items-start gap-3">
        <input
          type="radio"
          name="sequence-template"
          className="mt-1 h-3.5 w-3.5 text-nx-brand focus:ring-nx-brand/25"
          checked={selected}
          onChange={() => onSelect(t)}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-nx-ink">{t.name}</span>
            <span
              className={[
                'rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
                isIa ? 'bg-zinc-100 text-zinc-700' : 'bg-nx-card-muted text-nx-muted',
              ].join(' ')}
            >
              {isIa ? 'IA adaptativa' : 'Fija'}
            </span>
            <span
              className={[
                'rounded-full px-2 py-0.5 text-[10px] font-semibold tabular-nums',
                badge.tone === 'good'
                  ? 'bg-emerald-100 text-emerald-800'
                  : badge.tone === 'ok'
                    ? 'bg-zinc-100 text-zinc-700'
                    : 'bg-nx-card-muted text-nx-muted',
              ].join(' ')}
              title={badge.sample ? `Sobre ${badge.sample} contactos` : 'Todavía no hay contactos suficientes'}
            >
              {badge.text}
            </span>
            {!t.is_system ? (
              <button
                type="button"
                className="ml-auto text-[11px] font-medium text-red-500 hover:text-red-700"
                onClick={(e) => {
                  e.preventDefault()
                  onDelete(t)
                }}
              >
                Eliminar
              </button>
            ) : null}
          </div>

          {isIa ? (
            <p className="mt-1.5 text-[11px] text-nx-muted">
              La IA elige el mejor canal de cada toque según el prospecto y los resultados. Mensajes cortos.
            </p>
          ) : (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {(t.steps || []).map((s) => (
                <span
                  key={s.day}
                  className="inline-flex items-center gap-1 rounded-md bg-nx-card-muted px-1.5 py-0.5 text-[10px] text-nx-muted"
                  title={`Día ${s.day} · ${channelLabel(s.channel)}`}
                >
                  <span className={['h-2 w-2 rounded-full', CHANNEL_DOT[s.channel] || 'bg-nx-subtle'].join(' ')} />
                  D{s.day}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </label>
  )
}

/**
 * Selector de plantilla de secuencia + editor "crear nueva".
 * Personalizada: 1–7 toques (cadencia Nexus) + canal por toque.
 * Follow-up: solo canal si la campaña tiene follow-up activo (pregunta única en ICP).
 *
 * @param {{
 *   companyId: number,
 *   value: object | null,
 *   onChange: (plan: object | null) => void,
 *   allowedChannels?: string[],
 *   campaignFollowupEnabled?: boolean,
 * }} props
 */
export function SequenceTemplatePicker({
  companyId,
  value,
  onChange,
  allowedChannels = [],
  campaignFollowupEnabled = true,
}) {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const [editing, setEditing] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [draftSteps, setDraftSteps] = useState(() => defaultDraftSteps(MAX_TOUCHES))
  const [draftFollowUpChannel, setDraftFollowUpChannel] = useState('auto')
  const [savingTpl, setSavingTpl] = useState(false)

  const selectedId = value?.template_id ?? (value ? '__custom__' : 'nexus_3_li_email_wa')
  const draftTouchCount = draftSteps.length

  const load = useCallback(async () => {
    if (!companyId) return
    setLoading(true)
    setError(null)
    try {
      const rows = await fetchSequenceTemplates(companyId)
      setTemplates(Array.isArray(rows) ? rows : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void load()
  }, [load])

  // Si no hay plan aún, fijar Nexus 7 (o la primera) para que el radio seleccionado coincida con el valor real.
  useEffect(() => {
    if (value || !templates.length) return
    const preferred =
      templates.find((t) => t.template_id === 'nexus_3_li_email_wa') ||
      templates.find((t) => t.template_id === 'nexus_7') ||
      templates[0]
    if (preferred) onChange(planFromTemplate(preferred, campaignFollowupEnabled))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo al cargar plantillas sin valor
  }, [templates, value])

  // Sync follow_up.enabled con la pregunta única del ICP (evitar loop con onChange inline).
  useEffect(() => {
    if (!value || typeof value !== 'object') return
    const enabled = Boolean(campaignFollowupEnabled)
    const current = Boolean(value.follow_up?.enabled)
    if (current === enabled) return
    onChange({
      ...value,
      follow_up: {
        enabled,
        channel: value.follow_up?.channel || 'auto',
      },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo reaccionar al toggle del ICP
  }, [campaignFollowupEnabled])

  const allowedSet = useMemo(
    () => new Set((allowedChannels || []).map((c) => String(c).toLowerCase())),
    [allowedChannels],
  )

  const draftWarnings = useMemo(() => {
    if (allowedSet.size === 0) return []
    const missing = new Set()
    for (const s of draftSteps) {
      if (!allowedSet.has(s.channel)) missing.add(s.channel)
    }
    return [...missing]
  }, [draftSteps, allowedSet])

  function selectTemplate(t) {
    setEditing(false)
    onChange(planFromTemplate(t, campaignFollowupEnabled))
  }

  function startEditor() {
    const base = value && value.template_id == null ? value : null
    setDraftName(base?.template_name && base.template_name !== 'Secuencia personalizada' ? base.template_name : '')
    setDraftSteps(base?.steps?.length ? base.steps.map((s) => ({ ...s })) : defaultDraftSteps(MAX_TOUCHES))
    setDraftFollowUpChannel(base?.follow_up?.channel || 'auto')
    setEditing(true)
  }

  function setTouchCount(n) {
    const count = Math.min(MAX_TOUCHES, Math.max(1, Number(n) || 1))
    setDraftSteps((prev) => {
      const next = defaultDraftSteps(count)
      return next.map((s, i) => ({
        day: s.day,
        channel: prev[i]?.channel || s.channel,
      }))
    })
  }

  function setStepChannel(day, channel) {
    setDraftSteps((prev) => prev.map((s) => (s.day === day ? { ...s, channel } : s)))
  }

  function buildDraftPlan(templateId = null, name = null) {
    return {
      template_id: templateId,
      template_name: name || draftName.trim() || 'Secuencia personalizada',
      mode: 'fixed',
      steps: draftSteps.map((s) => ({ day: s.day, channel: s.channel })),
      follow_up: {
        enabled: Boolean(campaignFollowupEnabled),
        channel: campaignFollowupEnabled ? draftFollowUpChannel || 'auto' : 'auto',
      },
    }
  }

  function useWithoutSaving() {
    onChange(buildDraftPlan(null))
    setEditing(false)
  }

  async function saveAsTemplate() {
    if (!draftName.trim()) {
      setError('Ponele un nombre a tu secuencia para guardarla.')
      return
    }
    setSavingTpl(true)
    setError(null)
    try {
      const created = await createSequenceTemplate(companyId, {
        name: draftName.trim(),
        mode: 'fixed',
        steps: draftSteps.map((s) => ({ day: s.day, channel: s.channel })),
        follow_up: {
          enabled: Boolean(campaignFollowupEnabled),
          channel: campaignFollowupEnabled ? draftFollowUpChannel || 'auto' : 'auto',
        },
      })
      await load()
      onChange(planFromTemplate(created, campaignFollowupEnabled))
      setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSavingTpl(false)
    }
  }

  async function handleDelete(t) {
    const numericId = Number(String(t.template_id).replace('custom:', ''))
    if (!Number.isFinite(numericId)) return
    try {
      await deleteSequenceTemplate(companyId, numericId)
      if (selectedId === t.template_id) {
        const fallback = templates.find((x) => x.template_id === 'nexus_7')
        if (fallback) onChange(planFromTemplate(fallback, campaignFollowupEnabled))
      }
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const inlineCustomSelected = value && value.template_id == null && !editing

  return (
    <div className="space-y-3">
      <div>
        <p className="text-xs font-medium text-nx-ink">Secuencia de contacto</p>
        <p className="mt-0.5 text-[11px] text-nx-subtle">
          Elegí plantilla o armá la tuya: hasta {MAX_TOUCHES} toques, canal por toque. La secuencia se ejecuta
          tal como la configures.
        </p>
      </div>

      {error ? (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-700">{error}</p>
      ) : null}

      {loading ? (
        <p className="text-[11px] text-nx-subtle">Cargando secuencias…</p>
      ) : !companyId ? (
        <p className="text-[11px] text-nx-subtle">Seleccioná una empresa para ver las plantillas.</p>
      ) : (
        <div className="space-y-2">
          {templates.map((t) => (
            <TemplateCard
              key={t.template_id}
              t={t}
              selected={!editing && selectedId === t.template_id}
              onSelect={selectTemplate}
              onDelete={handleDelete}
            />
          ))}

          {inlineCustomSelected ? (
            <div className="rounded-xl border border-nx-brand bg-nx-brand/5 p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-nx-ink">
                  {value.template_name || 'Secuencia personalizada'} · {(value.steps || []).length} toques · sin
                  guardar
                </span>
                <button
                  type="button"
                  className="text-[11px] font-medium text-nx-brand hover:text-nx-brand-hover"
                  onClick={startEditor}
                >
                  Editar
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {(value.steps || []).map((s) => (
                  <span
                    key={s.day}
                    className="inline-flex items-center gap-1 rounded-md bg-white px-1.5 py-0.5 text-[10px] text-nx-muted"
                  >
                    <span className={['h-2 w-2 rounded-full', CHANNEL_DOT[s.channel] || 'bg-nx-subtle'].join(' ')} />
                    D{s.day} · {channelLabel(s.channel)}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}

      {!editing ? (
        <button
          type="button"
          className="w-full rounded-lg border border-dashed border-nx-border-strong px-3 py-2.5 text-sm font-medium text-nx-ink hover:border-nx-brand hover:text-nx-brand"
          onClick={startEditor}
        >
          + Crear tu propia secuencia
        </button>
      ) : (
        <div className="rounded-xl border border-nx-border bg-nx-card-muted p-4 space-y-3">
          <div>
            <label className="text-xs font-medium text-nx-ink">Nombre de la secuencia</label>
            <input
              className="mt-1 w-full rounded-lg border border-nx-border bg-white px-3 py-2 text-sm text-nx-ink focus:border-nx-brand focus:outline-none focus:ring-2 focus:ring-nx-brand/20"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              placeholder="Ej. Solo LinkedIn + WhatsApp"
            />
          </div>

          <div>
            <label htmlFor="seq-touch-count" className="text-xs font-medium text-nx-ink">
              Cantidad de toques
            </label>
            <p className="mt-0.5 text-[11px] text-nx-subtle">
              Máximo {MAX_TOUCHES}. La cadencia sigue los días Nexus (D1, D4, D7…).
            </p>
            <select
              id="seq-touch-count"
              className="mt-1 rounded-lg border border-nx-border bg-white px-3 py-2 text-sm text-nx-ink focus:border-nx-brand focus:outline-none"
              value={draftTouchCount}
              onChange={(e) => setTouchCount(e.target.value)}
            >
              {SEQUENCE_TOUCH_DAYS.map((_, i) => (
                <option key={i + 1} value={i + 1}>
                  {i + 1} toque{i === 0 ? '' : 's'}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-subtle">
              {draftTouchCount} toque{draftTouchCount === 1 ? '' : 's'} · canal por día
            </p>
            {draftSteps.map((s, idx) => (
              <div key={s.day} className="flex items-center gap-3">
                <span className="w-20 shrink-0 text-xs text-nx-muted">
                  Toque {idx + 1} · D{s.day}
                </span>
                <div className="flex flex-1 flex-wrap gap-1.5">
                  {CHANNELS.map((ch) => (
                    <button
                      key={ch}
                      type="button"
                      onClick={() => setStepChannel(s.day, ch)}
                      className={[
                        'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors',
                        s.channel === ch
                          ? CHANNEL_SELECTED[ch]
                          : 'border-nx-border bg-white text-nx-muted hover:border-nx-border-strong',
                      ].join(' ')}
                    >
                      <span className={['h-2 w-2 rounded-full', CHANNEL_DOT[ch]].join(' ')} />
                      {channelLabel(ch)}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {campaignFollowupEnabled ? (
            <div className="border-t border-nx-border pt-3">
              <p className="text-xs font-medium text-nx-ink">Canal del follow-up</p>
              <p className="mt-0.5 text-[11px] text-nx-subtle">
                El follow-up está activo en la campaña (pregunta del ICP). Elegí el canal de recontacto.
              </p>
              <select
                className="mt-2 rounded-lg border border-nx-border bg-white px-2 py-1.5 text-xs text-nx-ink focus:border-nx-brand focus:outline-none"
                value={draftFollowUpChannel}
                onChange={(e) => setDraftFollowUpChannel(e.target.value)}
              >
                {FOLLOWUP_CHANNELS.map((ch) => (
                  <option key={ch} value={ch}>
                    {followupLabel(ch)}
                  </option>
                ))}
              </select>
            </div>
          ) : null}

          {draftWarnings.length > 0 ? (
            <p className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-[11px] text-zinc-800">
              Ojo: usás {draftWarnings.map(channelLabel).join(', ')} pero no está en los canales permitidos de
              la campaña. Si el canal no está disponible en un prospecto, Nexus remapea a un canal habilitado.
            </p>
          ) : null}

          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              className="rounded-lg border border-nx-border bg-white px-3 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
              onClick={() => setEditing(false)}
            >
              Cancelar
            </button>
            <button
              type="button"
              className="rounded-lg border border-nx-brand bg-white px-3 py-2 text-sm font-medium text-nx-brand hover:bg-nx-brand/5"
              onClick={useWithoutSaving}
            >
              Usar sin guardar
            </button>
            <button
              type="button"
              disabled={savingTpl}
              className="nx-btn nx-btn-primary px-3 py-2 text-sm"
              onClick={saveAsTemplate}
            >
              {savingTpl ? 'Guardando…' : 'Guardar como plantilla'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
