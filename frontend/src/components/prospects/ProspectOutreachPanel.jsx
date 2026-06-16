import { useCallback, useEffect, useState } from 'react'
import { AlertBanner } from '../AlertBanner.jsx'
import { Modal } from '../Modal.jsx'
import { OpenAIDiagnosticsPanel } from '../outreach/OpenAIDiagnosticsPanel.jsx'
import { SdrValidationDebugPanel } from '../outreach/SdrValidationDebugPanel.jsx'
import { SequenceConversationPanel } from './SequenceConversationPanel.jsx'
import { SequenceProgressTimeline } from './SequenceProgressTimeline.jsx'
import { SequenceTouchHistory } from './SequenceTouchHistory.jsx'
import {
  ApiRequestError,
  enrichProspect,
  executeProspectSequenceTouch,
  fetchProspectOutreachContext,
  fetchProspectSequencePreview,
  fetchProspectSequenceTracking,
  generateProspectSequencePreview,
  patchProspect,
  resetProspectSequenceDraft,
  simulateProspectSequenceResponse,
  skipProspectSequenceTouch,
  startProspectSequence,
} from '../../utils/api.js'
import { fmtDateTime } from '../../utils/ownershipUi.js'

function ChecklistItem({ item }) {
  const ok = item.ok
  const optional = item.optional
  const badgeClass = ok
    ? 'bg-emerald-50 text-emerald-800 ring-emerald-600/20'
    : optional
      ? 'bg-slate-100 text-slate-600 ring-slate-500/20'
      : 'bg-amber-50 text-amber-800 ring-amber-600/20'
  const statusLabel = ok ? 'OK' : optional ? 'Opcional' : 'Falta'

  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-[#e5e7eb] bg-white px-3 py-2 text-sm">
      <div>
        <p className="font-medium text-[#111827]">{item.label}</p>
        {item.detail ? <p className="mt-0.5 text-xs text-[#6b7280]">{item.detail}</p> : null}
      </div>
      <span
        className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${badgeClass}`}
      >
        {statusLabel}
      </span>
    </div>
  )
}

const SEQUENCE_STATUS_LABELS = {
  sin_secuencia: 'Sin secuencia',
  borrador_listo: 'Borrador listo',
  borrador_corrupto: 'Borrador corrupto',
  en_curso: 'En curso',
  finalizada: 'Finalizada',
}

function SequenceDebugPanel({ debug }) {
  if (!debug) {
    return null
  }
  const statusLabel = SEQUENCE_STATUS_LABELS[debug.sequence_status] || debug.sequence_status
  const rows = [
    ['Prospect ID', debug.prospect_id],
    ['Sequence ID', debug.sequence_id ?? '— (embebida en prospecto)'],
    ['Ownership', debug.ownership_status],
    ['Estado secuencia', statusLabel],
    ['Borrador usable', debug.has_usable_draft ? 'Sí' : 'No'],
    ['Borrador raw en BD', debug.has_draft_raw ? 'Sí' : 'No'],
    ['Borrador corrupto', debug.draft_is_corrupt ? 'Sí' : 'No'],
    ['Toques en borrador', debug.draft_touch_count],
    ['Entradas touch log', debug.touch_log_entries],
    ['Tiene timeline', debug.has_timeline ? 'Sí' : 'No'],
  ]
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-xs text-slate-700">
      <p className="mb-2 font-semibold uppercase tracking-wide text-slate-500">Depuración secuencia</p>
      <dl className="grid gap-1 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-2 border-b border-slate-200/80 py-1">
            <dt className="text-slate-500">{label}</dt>
            <dd className="font-mono text-right text-slate-900">{String(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function ChannelReadinessPanel({ readiness }) {
  if (!readiness) {
    return null
  }
  const required = readiness.channels_required ?? 2
  const total = readiness.channels_total ?? 3
  const count = readiness.channel_count ?? 0
  const details = readiness.channels_detail || []
  const channelsOk = count >= required

  return (
    <div className="rounded-lg border border-[#e5e7eb] bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
          Canales detectados
        </h4>
        <span
          className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
            channelsOk
              ? 'bg-emerald-50 text-emerald-800 ring-emerald-600/20'
              : 'bg-amber-50 text-amber-900 ring-amber-600/20'
          }`}
        >
          {count}/{total} válidos · mínimo {required}
        </span>
      </div>
      <ul className="mt-2 space-y-1.5">
        {details.map((ch) => (
          <li key={ch.key} className="flex items-start gap-2 text-sm text-[#374151]">
            <span
              className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                ch.ok ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-400'
              }`}
              aria-hidden
            >
              {ch.ok ? '✓' : '✗'}
            </span>
            <div>
              <span className="font-medium text-[#111827]">{ch.label}</span>
              {ch.detail ? (
                <p className="text-xs text-[#6b7280]">{ch.detail}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {readiness.channels_summary ? (
        <p className="mt-2 text-xs text-[#6b7280]">{readiness.channels_summary}</p>
      ) : null}
      {!channelsOk ? (
        <p className="mt-2 text-xs text-amber-800">
          LinkedIn no es obligatorio por sí solo. Combiná al menos {required} canales, por ejemplo
          Email + WhatsApp o Email + LinkedIn.
        </p>
      ) : null}
    </div>
  )
}

export function ProspectOutreachPanel({ prospect, open, mode = 'view', onClose, onUpdated }) {
  const [context, setContext] = useState(null)
  const [preview, setPreview] = useState(null)
  const [tracking, setTracking] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [touchBusyDay, setTouchBusyDay] = useState(null)
  const [error, setError] = useState(null)
  const [validationDebug, setValidationDebug] = useState(null)
  const [simulateOpen, setSimulateOpen] = useState(false)
  const [simulateMessage, setSimulateMessage] = useState('')
  const [simulateBusy, setSimulateBusy] = useState(false)
  const [lastSimulation, setLastSimulation] = useState(null)
  const [generationStatus, setGenerationStatus] = useState(null)
  const [openaiRetryDay, setOpenaiRetryDay] = useState(null)
  const [form, setForm] = useState({
    campaign_id: '',
    email: '',
    linkedin_url: '',
    phone: '',
    whatsapp: '',
    company_website: '',
  })

  const loadContext = useCallback(async () => {
    if (!prospect?.id) {
      return null
    }
    const ctx = await fetchProspectOutreachContext(prospect.id)
    setContext(ctx)
    setForm({
      campaign_id: ctx.campaign_id ? String(ctx.campaign_id) : '',
      email: ctx.prospect_email || '',
      linkedin_url: ctx.prospect_linkedin || '',
      phone: ctx.prospect_phone || '',
      whatsapp: ctx.prospect_whatsapp || '',
      company_website: ctx.prospect_company_website || '',
    })
    return ctx
  }, [prospect?.id])

  const loadPanel = useCallback(async () => {
    if (!prospect?.id) {
      return
    }
    setLoading(true)
    setError(null)
    setValidationDebug(null)
    setPreview(null)
    setTracking(null)
    try {
      const ctx = await loadContext()
      if (mode === 'generate') {
        if (!ctx?.readiness?.is_ready) {
          setError(
            ctx?.readiness?.channels_summary ||
              ctx?.readiness?.missing_summary ||
              'Completá los datos antes de generar la secuencia',
          )
          return
        }
        if (!ctx?.can_generate_sequence) {
          setError(
            ctx?.generate_sequence_block_reason ||
              'No podés generar la secuencia con el estado actual del prospecto',
          )
          return
        }
        const data = await generateProspectSequencePreview(prospect.id)
        setPreview(data)
        await onUpdated?.()
      } else if (mode === 'view') {
        const viewErrors = []
        let trackingData = null
        let previewData = null
        try {
          trackingData = await fetchProspectSequenceTracking(prospect.id)
        } catch (e) {
          viewErrors.push(
            `Seguimiento: ${e instanceof Error ? e.message : String(e)}`,
          )
        }
        try {
          previewData = await fetchProspectSequencePreview(prospect.id)
        } catch (e) {
          viewErrors.push(`Vista previa: ${e instanceof Error ? e.message : String(e)}`)
        }
        setTracking(trackingData)
        setPreview(previewData)
        const hasVisible =
          (trackingData?.steps?.length ?? 0) > 0 || (previewData?.touches?.length ?? 0) > 0
        if (!hasVisible) {
          setError(
            viewErrors.join(' · ') ||
              ctx?.generate_sequence_block_reason ||
              'No hay secuencia visible — generá o regenerá la secuencia',
          )
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [prospect?.id, mode, loadContext, onUpdated])

  const refreshTracking = useCallback(async () => {
    if (!prospect?.id) {
      return null
    }
    const trackingData = await fetchProspectSequenceTracking(prospect.id)
    setTracking(trackingData)
    return trackingData
  }, [prospect?.id])

  useEffect(() => {
    if (open && prospect?.id) {
      void loadPanel()
    }
  }, [open, prospect?.id, mode, loadPanel])

  const generationBusy = busy || touchBusyDay != null || simulateBusy

  useEffect(() => {
    if (!generationBusy) {
      setGenerationStatus(null)
      return undefined
    }
    setGenerationStatus('Generando…')
    const timer = window.setTimeout(() => {
      setGenerationStatus('Reintentando generación…')
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [generationBusy])

  useEffect(() => {
    if (!tracking?.steps?.length || validationDebug) {
      return
    }
    const failedRetryable = tracking.steps.find(
      (s) => s.can_execute && s.touch_status === 'fallido' && s.validation_rejection,
    )
    if (failedRetryable?.validation_rejection) {
      setValidationDebug(failedRetryable.validation_rejection)
      setError((prev) =>
        prev || 'Último intento falló — revisá el borrador rechazado abajo.',
      )
    }
  }, [tracking, validationDebug])

  async function handleSaveSetup() {
    if (!prospect?.id) {
      return
    }
    setBusy(true)
    try {
      setError(null)
      const payload = {
        email: form.email.trim() || null,
        linkedin_url: form.linkedin_url.trim() || null,
        phone: form.phone.trim() || null,
        whatsapp: form.whatsapp.trim() || null,
        company_website: form.company_website.trim() || null,
      }
      if (form.campaign_id) {
        payload.campaign_id = Number(form.campaign_id)
      }
      await patchProspect(prospect.id, payload)
      await loadContext()
      await onUpdated?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleEnrich() {
    if (!prospect?.id) {
      return
    }
    setBusy(true)
    try {
      setError(null)
      await enrichProspect(prospect.id)
      await loadContext()
      await onUpdated?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleGenerateFromPanel({ forceRegenerate = false } = {}) {
    if (!prospect?.id) {
      return
    }
    if (!readiness?.is_ready) {
      setError(
        readiness?.channels_summary ||
          readiness?.missing_summary ||
          'Completá los datos antes de generar la secuencia',
      )
      return
    }
    if (!forceRegenerate && !context?.can_generate_sequence) {
      setError(
        context?.generate_sequence_block_reason ||
          'No podés generar la secuencia con el estado actual del prospecto',
      )
      return
    }
    setBusy(true)
    setOpenaiRetryDay(null)
    try {
      setError(null)
      const data = await generateProspectSequencePreview(prospect.id, { forceRegenerate })
      setPreview(data)
      await loadContext()
      await onUpdated?.()
    } catch (e) {
      if (e instanceof ApiRequestError && e.retryable) {
        setOpenaiRetryDay(null)
      }
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleResetDraft() {
    if (!prospect?.id) {
      return
    }
    setBusy(true)
    try {
      setError(null)
      await resetProspectSequenceDraft(prospect.id)
      setPreview(null)
      setTracking(null)
      await loadContext()
      await onUpdated?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleExecuteTouch(day) {
    if (!prospect?.id) {
      return
    }
    setTouchBusyDay(day)
    setOpenaiRetryDay(null)
    try {
      setError(null)
      setValidationDebug(null)
      const result = await executeProspectSequenceTouch(prospect.id, day)
      setTracking(result.tracking)
      setOpenaiRetryDay(null)
      if (result.fallback_test) {
        setError(
          'OpenAI en rate limit — se usó mensaje mock [FALLBACK TEST]. La secuencia siguió. Revisá diagnóstico abajo.',
        )
      }
      await onUpdated?.()
    } catch (e) {
      let rejection = e instanceof ApiRequestError ? e.validation : null
      const trackingData = await refreshTracking()
      if (!rejection && trackingData?.steps) {
        const failedStep = trackingData.steps.find(
          (s) => s.day === day && s.validation_rejection,
        )
        rejection = failedStep?.validation_rejection ?? null
      }
      setValidationDebug(rejection)
      const errMsg = e instanceof Error ? e.message : String(e)
      if (e instanceof ApiRequestError && e.retryable) {
        setOpenaiRetryDay(day)
        setTracking(trackingData)
        setError(
          `${errMsg} El toque sigue pendiente — podés reintentar cuando quieras.`,
        )
      } else {
        setError(
          rejection
            ? `${errMsg} — revisá el borrador rechazado abajo.`
            : errMsg,
        )
      }
    } finally {
      setTouchBusyDay(null)
    }
  }

  async function handleSkipTouch(day) {
    if (!prospect?.id) {
      return
    }
    setTouchBusyDay(day)
    try {
      setError(null)
      const result = await skipProspectSequenceTouch(prospect.id, day)
      setTracking(result.tracking)
      await onUpdated?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      await refreshTracking()
    } finally {
      setTouchBusyDay(null)
    }
  }

  async function handleSimulateResponse() {
    if (!prospect?.id || !simulateMessage.trim()) {
      return
    }
    setSimulateBusy(true)
    try {
      setError(null)
      setValidationDebug(null)
      const result = await simulateProspectSequenceResponse(prospect.id, {
        message: simulateMessage.trim(),
      })
      setTracking(result.tracking)
      setLastSimulation(result)
      setSimulateOpen(false)
      setSimulateMessage('')
      await onUpdated?.({
        includeTesting: true,
        commercialState: {
          commercial_state: result.commercial_state,
          commercial_state_label: result.commercial_state_label,
          commercial_state_is_testing: result.commercial_state_is_testing,
        },
      })
    } catch (e) {
      const testingHint =
        e instanceof ApiRequestError && e.testing?.enable_sequence_testing_hint
          ? ` ${e.testing.enable_sequence_testing_hint}`
          : ''
      setError((e instanceof Error ? e.message : String(e)) + testingHint)
      await refreshTracking()
    } finally {
      setSimulateBusy(false)
    }
  }

  async function handleStart() {
    if (!prospect?.id) {
      return
    }
    setBusy(true)
    try {
      setError(null)
      await startProspectSequence(prospect.id)
      await onUpdated?.()
      onClose?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!open || !prospect) {
    return null
  }

  const readiness = context?.readiness
  const sequenceDebug = context?.sequence_debug || tracking?.sequence_debug
  const isReady = readiness?.is_ready
  const canGenerate = context?.can_generate_sequence ?? prospect.can_generate_sequence
  const canView = context?.can_view_sequence ?? prospect.can_view_sequence
  const canStart = context?.can_start_sequence ?? prospect.can_start_sequence
  const showPrepare = mode === 'prepare' || (mode === 'generate' && !isReady && !preview)
  const showTimeline = mode === 'view' && Boolean(tracking?.steps?.length)
  const showPreviewDraft =
    mode === 'view' && Boolean(preview?.touches?.length) && !tracking?.steps?.length
  const hasSentTouch = Boolean(
    tracking?.history?.some(
      (s) => s.touch_status === 'enviado' || s.touch_status === 'respondido',
    ),
  )
  const sequenceTesting = tracking?.testing || context?.testing
  const testingEnabled = sequenceTesting?.sequence_testing_enabled === true
  const canSimulateResponse =
    showTimeline && tracking?.sequence_started_at && hasSentTouch && testingEnabled
  const titleByMode = {
    prepare: 'Preparar outreach',
    generate: 'Nexus Outreach',
    view: 'Nexus Outreach',
  }

  return (
    <Modal title={`${titleByMode[mode] || 'Nexus Outreach'} — ${prospect.name}`} onClose={onClose}>
      <AlertBanner message={error} onDismiss={() => setError(null)} />
      {generationStatus ? (
        <p className="mb-4 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
          {generationStatus}
        </p>
      ) : null}

      <div className="mb-4">
        <OpenAIDiagnosticsPanel autoLoad={open} />
      </div>
      {validationDebug ? (
        <div className="mb-4">
          <SdrValidationDebugPanel
            validation={validationDebug}
            onDismiss={() => setValidationDebug(null)}
          />
        </div>
      ) : null}

      {loading ? (
        <p className="text-sm text-[#6b7280]">
          {mode === 'generate' ? 'Cargando contexto...' : 'Cargando...'}
        </p>
      ) : (
        <div className="space-y-4">
          <div className="rounded-lg border border-[#e5e7eb] bg-[#f8fafc] p-3 text-sm">
            <p className="font-medium text-[#111827]">{context?.prospect_name || prospect.name}</p>
            <p className="text-[#6b7280]">{context?.prospect_company || prospect.company_name}</p>
            <dl className="mt-2 grid gap-1 text-xs text-[#374151] sm:grid-cols-2">
              <div>
                <dt className="text-[#9ca3af]">Campaña</dt>
                <dd>{context?.campaign_name || '—'}</dd>
              </div>
              <div>
                <dt className="text-[#9ca3af]">Producto</dt>
                <dd>{context?.product_name || '—'}</dd>
              </div>
              <div>
                <dt className="text-[#9ca3af]">Playbook</dt>
                <dd>{context?.playbook_name || preview?.playbook_name || 'SDR 21d MVP'}</dd>
              </div>
              <div>
                <dt className="text-[#9ca3af]">Canales</dt>
                <dd>{(context?.available_channels || []).join(', ') || '—'}</dd>
              </div>
            </dl>
          </div>

          <ChannelReadinessPanel readiness={readiness} />
          <SequenceDebugPanel debug={sequenceDebug} />

          {readiness?.checklist?.length ? (
            <div className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
                Checklist de readiness
              </h4>
              {readiness.checklist.map((item) => (
                <ChecklistItem key={item.key} item={item} />
              ))}
              {readiness.missing_summary ? (
                <p className="text-xs text-amber-700">{readiness.missing_summary}</p>
              ) : null}
              {context?.generate_sequence_block_reason && !canGenerate ? (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
                  {context.generate_sequence_block_reason}
                </p>
              ) : null}
              {context?.start_sequence_block_reason && !canStart ? (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
                  {context.start_sequence_block_reason}
                </p>
              ) : null}
              {sequenceDebug?.draft_is_corrupt || sequenceDebug?.sequence_status === 'borrador_corrupto' ? (
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleResetDraft()}
                    className="rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-900 hover:bg-red-100 disabled:opacity-50"
                  >
                    Limpiar borrador corrupto
                  </button>
                  {isReady ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleGenerateFromPanel({ forceRegenerate: true })}
                      className="rounded-lg border border-nx-brand/30 bg-nx-brand/5 px-3 py-1.5 text-xs font-medium text-nx-brand hover:bg-nx-brand/10 disabled:opacity-50"
                    >
                      Regenerar secuencia
                    </button>
                  ) : null}
                </div>
              ) : null}
              {!canGenerate && canView && isReady && !sequenceDebug?.draft_is_corrupt ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleGenerateFromPanel({ forceRegenerate: true })}
                  className="rounded-lg border border-nx-brand/30 bg-nx-brand/5 px-3 py-1.5 text-xs font-medium text-nx-brand hover:bg-nx-brand/10 disabled:opacity-50"
                >
                  Regenerar secuencia
                </button>
              ) : null}
            </div>
          ) : null}

          {showPrepare ? (
            <div className="space-y-3 rounded-lg border border-[#e5e7eb] bg-white p-3">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
                Completar datos
              </h4>
              <label className="block text-sm">
                <span className="mb-1 block text-[#374151]">Campaña</span>
                <select
                  value={form.campaign_id}
                  onChange={(ev) => setForm((f) => ({ ...f, campaign_id: ev.target.value }))}
                  className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
                >
                  <option value="">Seleccionar campaña</option>
                  {(context?.campaign_options || []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {c.product_name ? ` · ${c.product_name}` : ''}
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-sm">
                  <span className="mb-1 block text-[#374151]">Email</span>
                  <input
                    type="email"
                    value={form.email}
                    onChange={(ev) => setForm((f) => ({ ...f, email: ev.target.value }))}
                    className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-[#374151]">LinkedIn</span>
                  <input
                    type="url"
                    value={form.linkedin_url}
                    onChange={(ev) => setForm((f) => ({ ...f, linkedin_url: ev.target.value }))}
                    className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-[#374151]">Teléfono</span>
                  <input
                    type="text"
                    value={form.phone}
                    onChange={(ev) => setForm((f) => ({ ...f, phone: ev.target.value }))}
                    className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-[#374151]">WhatsApp</span>
                  <input
                    type="text"
                    value={form.whatsapp}
                    onChange={(ev) => setForm((f) => ({ ...f, whatsapp: ev.target.value }))}
                    className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
                  />
                </label>
              </div>
              <label className="block text-sm">
                <span className="mb-1 block text-[#374151]">Sitio web empresa</span>
                <input
                  type="url"
                  value={form.company_website}
                  onChange={(ev) => setForm((f) => ({ ...f, company_website: ev.target.value }))}
                  className="w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
                />
              </label>
              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleSaveSetup()}
                  className="rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm font-medium text-[#374151] hover:bg-[#f8fafc] disabled:opacity-50"
                >
                  Guardar
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleEnrich()}
                  className="rounded-lg border border-nx-brand/30 bg-nx-brand/5 px-3 py-2 text-sm font-medium text-nx-brand hover:bg-nx-brand/10 disabled:opacity-50"
                >
                  Enriquecer
                </button>
                {isReady ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleGenerateFromPanel()}
                    className="rounded-lg bg-nx-brand px-3 py-2 text-sm font-medium text-white hover:bg-nx-brand/90 disabled:opacity-50"
                  >
                    Generar Secuencia
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}

          {showTimeline ? (
            <>
              {sequenceTesting ? (
                <div
                  className={`rounded-lg border px-3 py-2 text-xs ${
                    testingEnabled
                      ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
                      : 'border-amber-200 bg-amber-50 text-amber-950'
                  }`}
                >
                  <p className="font-medium">
                    {testingEnabled ? 'Modo testing de secuencias activo' : 'Simulación de respuestas deshabilitada'}
                  </p>
                  <dl className="mt-1 grid gap-0.5 font-mono text-[10px] opacity-90">
                    <div>
                      NEXUS_REAL_MODE={sequenceTesting.env_nexus_real_mode || '(vacío)'}
                    </div>
                    <div>
                      NEXUS_DISABLE_OUTREACH_SIMULATION=
                      {sequenceTesting.env_nexus_disable_outreach_simulation || '(vacío)'}
                    </div>
                    <div>
                      NEXUS_ENABLE_SEQUENCE_TESTING=
                      {sequenceTesting.env_nexus_enable_sequence_testing || '(vacío)'}
                    </div>
                  </dl>
                  {!testingEnabled ? (
                    <p className="mt-1">{sequenceTesting.enable_sequence_testing_hint}</p>
                  ) : (
                    <p className="mt-1 opacity-80">
                      Configuración en <code className="text-[10px]">backend/.env</code> — reiniciá uvicorn
                      tras cambiar variables.
                    </p>
                  )}
                </div>
              ) : null}

              <div className="rounded-lg border border-nx-brand/20 bg-nx-brand/5 p-3 text-sm">
                <dl className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <dt className="text-xs text-[#6b7280]">Día actual</dt>
                    <dd className="font-semibold text-nx-brand">
                      {tracking.current_day_label || '—'}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-[#6b7280]">Próximo toque</dt>
                    <dd className="font-medium text-[#111827]">
                      {tracking.sequence_paused
                        ? 'Pausado — respondé al prospecto'
                        : tracking.next_touch_label || '—'}
                    </dd>
                    {!tracking.sequence_paused ? (
                      <dd className="text-xs text-[#6b7280]">{fmtDateTime(tracking.next_touch_at)}</dd>
                    ) : null}
                  </div>
                </dl>
                {canSimulateResponse ? (
                  <div className="mt-3 border-t border-nx-brand/10 pt-3">
                    <button
                      type="button"
                      disabled={simulateBusy || busy}
                      onClick={() => setSimulateOpen(true)}
                      className="rounded-lg border border-violet-300 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-900 hover:bg-violet-100 disabled:opacity-50"
                    >
                      Simular respuesta
                    </button>
                    <p className="mt-1 text-[10px] text-[#6b7280]">
                      Modo testing — escribí la réplica del prospecto para pausar la secuencia y ver sugerencia IA.
                    </p>
                  </div>
                ) : hasSentTouch && !testingEnabled ? (
                  <p className="mt-3 border-t border-nx-brand/10 pt-3 text-[10px] text-amber-800">
                    Simular respuesta no disponible — activá{' '}
                    <code className="font-mono">NEXUS_ENABLE_SEQUENCE_TESTING=1</code> en backend/.env
                  </p>
                ) : null}
              </div>

              <SequenceConversationPanel tracking={tracking} lastSimulation={lastSimulation} />

              <div className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
                  Progreso de secuencia
                </h4>
                <SequenceProgressTimeline steps={tracking.steps} />
              </div>

              <SequenceTouchHistory
                steps={tracking.steps}
                history={tracking.history}
                busyDay={touchBusyDay}
                generationStatus={generationStatus}
                openaiRetryDay={openaiRetryDay}
                onExecute={(day) => void handleExecuteTouch(day)}
                onSkip={(day) => void handleSkipTouch(day)}
              />
            </>
          ) : null}

          {canStart ? (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void handleStart()}
                className="rounded-lg bg-nx-brand px-3 py-2 text-sm font-medium text-white hover:bg-nx-brand/90 disabled:opacity-50"
              >
                Iniciar secuencia
              </button>
            </div>
          ) : null}

          {showPreviewDraft ? (
            <div className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
                Secuencia — {preview.playbook_name}
              </h4>
              {preview.touches.map((t) => (
                <div
                  key={t.day}
                  className="rounded-lg border border-[#e5e7eb] bg-white p-3 text-sm"
                >
                  <p className="font-medium text-[#111827]">
                    Día {t.day} · {t.channel}
                  </p>
                  <p className="mt-1 text-xs text-[#6b7280]">{t.objective}</p>
                  <p className="mt-2 whitespace-pre-wrap text-xs text-[#374151]">{t.body_preview}</p>
                </div>
              ))}
            </div>
          ) : mode === 'view' && !showTimeline && !showPreviewDraft ? (
            <p className="text-xs text-[#6b7280]">No hay toques para mostrar.</p>
          ) : null}

          {(tracking?.sequence_started_at || prospect.sequence_start_at || prospect.next_touch_at) ? (
            <div className="rounded-lg border border-dashed border-[#e5e7eb] p-3 text-xs text-[#6b7280]">
              <p>Inicio: {fmtDateTime(tracking?.sequence_started_at || prospect.sequence_start_at)}</p>
              <p>Próximo toque: {tracking?.next_touch_label || prospect.next_touch_label || '—'}</p>
              <p>{fmtDateTime(tracking?.next_touch_at || prospect.next_touch_at)}</p>
            </div>
          ) : null}
        </div>
      )}

      {simulateOpen ? (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-xl border border-[#e5e7eb] bg-white p-4 shadow-xl">
            <h3 className="text-sm font-semibold text-[#111827]">Simular respuesta del prospecto</h3>
            <p className="mt-1 text-xs text-[#6b7280]">
              Escribí el mensaje como si lo hubiera enviado el prospecto. Nexus pausará la secuencia,
              clasificará la respuesta y sugerirá tu próximo mensaje.
            </p>
            <textarea
              value={simulateMessage}
              onChange={(e) => setSimulateMessage(e.target.value)}
              rows={5}
              placeholder="Ej: Sí, me interesa, mandame más info."
              className="mt-3 w-full rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm"
            />
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                disabled={simulateBusy}
                onClick={() => {
                  setSimulateOpen(false)
                  setSimulateMessage('')
                }}
                className="rounded-lg border border-[#e5e7eb] px-3 py-2 text-sm text-[#374151] hover:bg-[#f8fafc] disabled:opacity-50"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={simulateBusy || !simulateMessage.trim()}
                onClick={() => void handleSimulateResponse()}
                className="rounded-lg bg-violet-700 px-3 py-2 text-sm font-medium text-white hover:bg-violet-800 disabled:opacity-50"
              >
                {simulateBusy ? 'Procesando…' : 'Registrar respuesta'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </Modal>
  )
}
