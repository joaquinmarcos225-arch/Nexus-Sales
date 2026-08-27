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
  markLinkedInAssistedSent,
  markProspectSequenceTouchSent,
  patchProspect,
  registerLinkedInInbound,
  resetProspectSequenceDraft,
  simulateProspectSequenceResponse,
  skipProspectSequenceTouch,
  startProspectSequence,
} from '../../utils/api.js'
import { notifyLinkedInQueueChanged } from '../../hooks/useLinkedInPending.js'
import { fmtDateTime } from '../../utils/ownershipUi.js'
import { showOpsDebug } from '../../utils/opsDebug.js'

function clearStepErrors(tracking, day) {
  if (!tracking?.steps?.length) {
    return tracking
  }
  return {
    ...tracking,
    steps: tracking.steps.map((step) =>
      step.day === day
        ? {
            ...step,
            validation_rejection: null,
            error_message: null,
            openai_last_error: null,
          }
        : step,
    ),
  }
}

function stripRetryableTouchErrors(tracking) {
  if (!tracking?.steps?.length) {
    return tracking
  }
  return {
    ...tracking,
    steps: tracking.steps.map((step) =>
      step.can_execute && step.touch_status !== 'fallido'
        ? {
            ...step,
            validation_rejection: null,
            error_message: null,
            openai_last_error: null,
          }
        : step,
    ),
  }
}

function ChecklistItem({ item }) {
  const ok = item.ok
  const optional = item.optional
  const badgeClass = ok
    ? 'bg-red-50 text-red-800 ring-red-600/20'
    : optional
      ? 'bg-nx-card-muted text-nx-muted ring-nx-muted/20'
      : 'bg-zinc-50 text-zinc-800 ring-zinc-600/20'
  const statusLabel = ok ? 'OK' : optional ? 'Opcional' : 'Falta'

  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-nx-border bg-white px-3 py-2 text-sm">
      <div>
        <p className="font-medium text-nx-ink">{item.label}</p>
        {item.detail ? <p className="mt-0.5 text-xs text-nx-muted">{item.detail}</p> : null}
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
    <div className="rounded-lg border border-dashed border-nx-border-strong bg-nx-card-muted p-3 text-xs text-nx-ink">
      <p className="mb-2 font-semibold uppercase tracking-wide text-nx-muted">Depuración secuencia</p>
      <dl className="grid gap-1 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-2 border-b border-nx-border/80 py-1">
            <dt className="text-nx-muted">{label}</dt>
            <dd className="font-mono text-right text-nx-ink">{String(value)}</dd>
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
    <div className="rounded-lg border border-nx-border bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
          Canales detectados
        </h4>
        <span
          className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
            channelsOk
              ? 'bg-red-50 text-red-800 ring-red-600/20'
              : 'bg-zinc-50 text-zinc-900 ring-zinc-600/20'
          }`}
        >
          {count}/{total} válidos · mínimo {required}
        </span>
      </div>
      <ul className="mt-2 space-y-1.5">
        {details.map((ch) => (
          <li key={ch.key} className="flex items-start gap-2 text-sm text-nx-ink">
            <span
              className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                ch.ok ? 'bg-red-100 text-red-800' : 'bg-nx-card-muted text-nx-subtle'
              }`}
              aria-hidden
            >
              {ch.ok ? '✓' : '✗'}
            </span>
            <div>
              <span className="font-medium text-nx-ink">{ch.label}</span>
              {ch.detail ? (
                <p className="text-xs text-nx-muted">{ch.detail}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {readiness.channels_summary ? (
        <p className="mt-2 text-xs text-nx-muted">{readiness.channels_summary}</p>
      ) : null}
      {!channelsOk ? (
        <p className="mt-2 text-xs text-zinc-800">
          LinkedIn no es obligatorio por sí solo. Necesitás al menos {required} canales en total
          (email, LinkedIn o WhatsApp). Si ya tenés uno, agregá otro — por ejemplo Email + WhatsApp
          o Email + LinkedIn.
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
  const [touchNotice, setTouchNotice] = useState(null)
  const [validationDebug, setValidationDebug] = useState(null)
  const [simulateOpen, setSimulateOpen] = useState(false)
  const [simulateMessage, setSimulateMessage] = useState('')
  const [simulateBusy, setSimulateBusy] = useState(false)
  const [linkedinInboundText, setLinkedinInboundText] = useState('')
  const [linkedinInboundBusy, setLinkedinInboundBusy] = useState(false)
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
        setTracking(trackingData ? stripRetryableTouchErrors(trackingData) : trackingData)
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
    const cleaned = trackingData ? stripRetryableTouchErrors(trackingData) : trackingData
    setTracking(cleaned)
    return cleaned
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
    setValidationDebug(null)
  }, [prospect?.id])

  useEffect(() => {
    if (!open || !prospect?.id) {
      return undefined
    }
    async function onExtensionSent(event) {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.type !== 'NEXUS_LINKEDIN_SENT_REGISTERED') return
      const pid = Number(data.payload?.prospectId)
      if (!pid || pid !== Number(prospect.id)) return
      setTouchNotice('LinkedIn marcado como enviado automáticamente.')
      try {
        await markLinkedInAssistedSent(pid)
      } catch {
        /* La extensión ya lo registró; refrescamos igual abajo. */
      }
      notifyLinkedInQueueChanged({ sent: true, prospectId: pid })
      void refreshTracking()
      void onUpdated?.()
    }
    window.addEventListener('message', onExtensionSent)
    return () => window.removeEventListener('message', onExtensionSent)
  }, [open, prospect?.id, onUpdated, refreshTracking])

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
      if (mode === 'view') {
        try {
          const trackingData = await fetchProspectSequenceTracking(prospect.id)
          setTracking(trackingData ? stripRetryableTouchErrors(trackingData) : trackingData)
        } catch {
          setTracking(null)
        }
      }
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
    setError(null)
    setTouchNotice(null)
    setValidationDebug(null)
    setTracking((prev) => (prev ? clearStepErrors(prev, day) : prev))
    try {
      const result = await executeProspectSequenceTouch(prospect.id, day)
      setTracking(result.tracking)
      setOpenaiRetryDay(null)
      if (result.linkedin_assisted || result.gmail_draft_created || result.whatsapp_sent || result.message) {
        setTouchNotice(
          result.message ||
            (result.gmail_draft_created
              ? 'Borrador creado en Gmail. Revisá, enviá manualmente y marcá como enviado.'
              : result.whatsapp_sent || result.whatsapp_assisted
                ? result.whatsapp_assisted
                  ? 'WhatsApp en cola (Web asistido). Abrí WhatsApp Web con la extensión Nexus para enviar.'
                  : result.whatsapp_dry_run
                    ? 'WhatsApp dry-run legacy (desactivar WHATSAPP_DRY_RUN).'
                    : 'WhatsApp enviado.'
                : 'Toque LinkedIn listo. Andá a Centro de outreach → Enviar mensaje.'),
        )
      }
      if (result.fallback_test) {
        setError(
          'OpenAI en rate limit — se usó mensaje mock [FALLBACK TEST]. La secuencia siguió. Revisá diagnóstico abajo.',
        )
      }
      await onUpdated?.()
    } catch (e) {
      const rejection = e instanceof ApiRequestError ? e.validation : null
      const trackingData = await refreshTracking()
      setTracking(trackingData)
      setValidationDebug(rejection)
      const errMsg = e instanceof Error ? e.message : String(e)
      if (e instanceof ApiRequestError && e.retryable) {
        setOpenaiRetryDay(day)
        setError(
          `${errMsg} El toque sigue pendiente — podés reintentar cuando quieras.`,
        )
      } else {
        setError(rejection ? `${errMsg} — revisá el borrador rechazado abajo.` : errMsg)
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

  async function handleMarkTouchSent(day) {
    if (!prospect?.id) {
      return
    }
    setTouchBusyDay(day)
    setTouchNotice(null)
    setTracking((prev) => {
      if (!prev?.steps?.length) {
        return prev
      }
      const steps = prev.steps.map((step) =>
        Number(step.day) === Number(day)
          ? {
              ...step,
              can_mark_sent: false,
              can_execute: false,
              touch_status: 'enviado',
              status: 'sent',
              status_label: 'Enviado',
            }
          : step,
      )
      return { ...prev, steps }
    })
    try {
      setError(null)
      const result = await markProspectSequenceTouchSent(prospect.id, day)
      setTracking(result.tracking)
      if (result.message) {
        setTouchNotice(result.message)
      }
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

  async function handleRegisterLinkedInInbound() {
    if (!prospect?.id || !linkedinInboundText.trim()) {
      return
    }
    setLinkedinInboundBusy(true)
    try {
      setError(null)
      const result = await registerLinkedInInbound(prospect.id, {
        message: linkedinInboundText.trim(),
      })
      setLinkedinInboundText('')
      setTouchNotice(
        result.reply_draft_ready
          ? 'Respuesta LinkedIn registrada. Revisá Centro de outreach → cola LinkedIn para responder.'
          : result.detail || 'Respuesta registrada.',
      )
      notifyLinkedInQueueChanged({ inbound: true, prospectId: prospect.id })
      const trackingData = await refreshTracking()
      setTracking(trackingData)
      await onUpdated?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLinkedinInboundBusy(false)
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
  const canRegenerateSequence =
    canView &&
    isReady &&
    !canGenerate &&
    !sequenceDebug?.draft_is_corrupt &&
    (!hasSentTouch || testingEnabled)
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
      {touchNotice ? (
        <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="flex-1">{touchNotice}</p>
          <button
            type="button"
            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-red-900 hover:bg-red-100"
            onClick={() => setTouchNotice(null)}
          >
            Cerrar
          </button>
        </div>
      ) : null}
      {generationStatus ? (
        <p className="mb-4 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900">
          {generationStatus}
        </p>
      ) : null}

      {showOpsDebug ? (
        <div className="mb-4">
          <OpenAIDiagnosticsPanel autoLoad={open} />
        </div>
      ) : null}
      {showOpsDebug && validationDebug ? (
        <div className="mb-4">
          <SdrValidationDebugPanel
            validation={validationDebug}
            onDismiss={() => setValidationDebug(null)}
          />
        </div>
      ) : null}

      {loading ? (
        <p className="text-sm text-nx-muted">
          {mode === 'generate' ? 'Cargando contexto...' : 'Cargando...'}
        </p>
      ) : (
        <div className="space-y-4">
          <div className="rounded-lg border border-nx-border bg-nx-card-muted p-3 text-sm">
            <p className="font-medium text-nx-ink">{context?.prospect_name || prospect.name}</p>
            <p className="text-nx-muted">{context?.prospect_company || prospect.company_name}</p>
            <dl className="mt-2 grid gap-1 text-xs text-nx-ink sm:grid-cols-2">
              <div>
                <dt className="text-nx-subtle">Campaña</dt>
                <dd>{context?.campaign_name || '—'}</dd>
              </div>
              <div>
                <dt className="text-nx-subtle">Producto</dt>
                <dd>{context?.product_name || '—'}</dd>
              </div>
              <div>
                <dt className="text-nx-subtle">Playbook</dt>
                <dd>{context?.playbook_name || preview?.playbook_name || 'SDR Nexus 7 toques'}</dd>
              </div>
              <div>
                <dt className="text-nx-subtle">Canales</dt>
                <dd>{(context?.available_channels || []).join(', ') || '—'}</dd>
              </div>
            </dl>
          </div>

          <ChannelReadinessPanel readiness={readiness} />
          {showOpsDebug ? <SequenceDebugPanel debug={sequenceDebug} /> : null}

          {canRegenerateSequence ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-nx-brand/25 bg-nx-brand/5 px-3 py-2.5">
              <p className="text-xs text-nx-ink">
                {hasSentTouch
                  ? 'Modo testing: podés regenerar el borrador y volver a iniciar la secuencia.'
                  : 'Regenerá el borrador si el mensaje del Día 1 no quedó bien.'}
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => void handleGenerateFromPanel({ forceRegenerate: true })}
                className="shrink-0 rounded-lg border border-nx-brand/40 bg-white px-3 py-1.5 text-xs font-semibold text-nx-brand hover:bg-nx-brand/10 disabled:opacity-50"
              >
                {busy ? 'Regenerando…' : 'Regenerar secuencia'}
              </button>
            </div>
          ) : null}

          {readiness?.checklist?.length ? (
            <div className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
                Checklist de readiness
              </h4>
              {readiness.checklist.map((item) => (
                <ChecklistItem key={item.key} item={item} />
              ))}
              {readiness.missing_summary ? (
                <p className="text-xs text-zinc-700">{readiness.missing_summary}</p>
              ) : null}
              {context?.generate_sequence_block_reason && !canGenerate ? (
                <p className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-950">
                  {context.generate_sequence_block_reason}
                </p>
              ) : null}
              {context?.start_sequence_block_reason && !canStart ? (
                <p className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-950">
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
            </div>
          ) : null}

          {showPrepare ? (
            <div className="space-y-3 rounded-lg border border-nx-border bg-white p-3">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
                Completar datos
              </h4>
              <label className="block text-sm">
                <span className="mb-1 block text-nx-ink">Campaña</span>
                <select
                  value={form.campaign_id}
                  onChange={(ev) => setForm((f) => ({ ...f, campaign_id: ev.target.value }))}
                  className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
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
                  <span className="mb-1 block text-nx-ink">Email</span>
                  <input
                    type="email"
                    value={form.email}
                    onChange={(ev) => setForm((f) => ({ ...f, email: ev.target.value }))}
                    className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-nx-ink">LinkedIn</span>
                  <input
                    type="url"
                    value={form.linkedin_url}
                    onChange={(ev) => setForm((f) => ({ ...f, linkedin_url: ev.target.value }))}
                    className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-nx-ink">Teléfono</span>
                  <input
                    type="text"
                    value={form.phone}
                    onChange={(ev) => setForm((f) => ({ ...f, phone: ev.target.value }))}
                    className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-nx-ink">WhatsApp</span>
                  <input
                    type="text"
                    value={form.whatsapp}
                    onChange={(ev) => setForm((f) => ({ ...f, whatsapp: ev.target.value }))}
                    className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
                  />
                </label>
              </div>
              <label className="block text-sm">
                <span className="mb-1 block text-nx-ink">Sitio web empresa</span>
                <input
                  type="url"
                  value={form.company_website}
                  onChange={(ev) => setForm((f) => ({ ...f, company_website: ev.target.value }))}
                  className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
                />
              </label>
              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleSaveSetup()}
                  className="rounded-lg border border-nx-border px-3 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted disabled:opacity-50"
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
                    className="nx-btn nx-btn-primary px-3 py-2 text-sm"
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
                      ? 'border-red-200 bg-red-50 text-red-900'
                      : 'border-zinc-200 bg-zinc-50 text-zinc-950'
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
                    <dt className="text-xs text-nx-muted">Día actual</dt>
                    <dd className="font-semibold text-nx-brand">
                      {tracking.current_day_label || '—'}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-nx-muted">Próximo toque</dt>
                    <dd className="font-medium text-nx-ink">
                      {tracking.sequence_paused
                        ? 'Pausado — respondé al prospecto'
                        : tracking.next_touch_label || '—'}
                    </dd>
                    {!tracking.sequence_paused ? (
                      <dd className="text-xs text-nx-muted">{fmtDateTime(tracking.next_touch_at)}</dd>
                    ) : null}
                  </div>
                </dl>
                {canSimulateResponse ? (
                  <div className="mt-3 border-t border-nx-brand/10 pt-3">
                    <button
                      type="button"
                      disabled={simulateBusy || busy}
                      onClick={() => setSimulateOpen(true)}
                      className="rounded-lg border border-zinc-300 bg-zinc-50 px-3 py-1.5 text-xs font-medium text-zinc-900 hover:bg-zinc-100 disabled:opacity-50"
                    >
                      Simular respuesta
                    </button>
                    <p className="mt-1 text-[10px] text-nx-muted">
                      Modo testing — escribí la réplica del prospecto para pausar la secuencia y ver sugerencia IA.
                    </p>
                  </div>
                ) : hasSentTouch && !testingEnabled ? (
                  <p className="mt-3 border-t border-nx-brand/10 pt-3 text-[10px] text-zinc-800">
                    Simular respuesta no disponible — activá{' '}
                    <code className="font-mono">NEXUS_ENABLE_SEQUENCE_TESTING=1</code> en backend/.env
                  </p>
                ) : null}
                {showTimeline && (prospect.linkedin_url || '').includes('linkedin.com/in/') ? (
                  <div className="mt-3 border-t border-nx-brand/10 pt-3 space-y-2">
                    <p className="text-xs font-medium text-nx-ink">Respuesta LinkedIn (automática)</p>
                    <p className="text-[10px] text-nx-muted">
                      Con la extensión Nexus y LinkedIn Messaging abiertos, las respuestas se detectan solas
                      y el borrador aparece en la cola. Este campo es solo respaldo si falló la detección.
                    </p>
                    <textarea
                      value={linkedinInboundText}
                      onChange={(e) => setLinkedinInboundText(e.target.value)}
                      rows={3}
                      placeholder="Respaldo: pegá el mensaje solo si la extensión no lo detectó."
                      className="w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
                    />
                    <button
                      type="button"
                      disabled={linkedinInboundBusy || !linkedinInboundText.trim()}
                      onClick={() => void handleRegisterLinkedInInbound()}
                      className="rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-50"
                    >
                      {linkedinInboundBusy ? 'Registrando…' : 'Respaldo: registrar a mano'}
                    </button>
                  </div>
                ) : null}
              </div>

              <SequenceConversationPanel tracking={tracking} lastSimulation={lastSimulation} />

              <div className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
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
                onMarkSent={(day) => void handleMarkTouchSent(day)}
              />
            </>
          ) : null}

          {canStart ? (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void handleStart()}
                className="nx-btn nx-btn-primary px-3 py-2 text-sm"
              >
                Iniciar secuencia
              </button>
            </div>
          ) : null}

          {showPreviewDraft ? (
            <div className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
                Secuencia — {preview.playbook_name}
              </h4>
              {preview.touches.map((t) => (
                <div
                  key={t.day}
                  className="rounded-lg border border-nx-border bg-white p-3 text-sm"
                >
                  <p className="font-medium text-nx-ink">
                    Día {t.day} · {t.channel}
                  </p>
                  <p className="mt-1 text-xs text-nx-muted">{t.objective}</p>
                  <p className="mt-2 whitespace-pre-wrap text-xs text-nx-ink">{t.body_preview}</p>
                </div>
              ))}
            </div>
          ) : mode === 'view' && !showTimeline && !showPreviewDraft ? (
            <p className="text-xs text-nx-muted">No hay toques para mostrar.</p>
          ) : null}

          {(tracking?.sequence_started_at || prospect.sequence_start_at || prospect.next_touch_at) ? (
            <div className="rounded-lg border border-dashed border-nx-border p-3 text-xs text-nx-muted">
              <p>Inicio: {fmtDateTime(tracking?.sequence_started_at || prospect.sequence_start_at)}</p>
              <p>Próximo toque: {tracking?.next_touch_label || prospect.next_touch_label || '—'}</p>
              <p>{fmtDateTime(tracking?.next_touch_at || prospect.next_touch_at)}</p>
            </div>
          ) : null}
        </div>
      )}

      {simulateOpen ? (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-xl border border-nx-border bg-white p-4 shadow-xl">
            <h3 className="text-sm font-semibold text-nx-ink">Simular respuesta del prospecto</h3>
            <p className="mt-1 text-xs text-nx-muted">
              Escribí el mensaje como si lo hubiera enviado el prospecto. Nexus pausará la secuencia,
              clasificará la respuesta y sugerirá tu próximo mensaje.
            </p>
            <textarea
              value={simulateMessage}
              onChange={(e) => setSimulateMessage(e.target.value)}
              rows={5}
              placeholder="Ej: Sí, me interesa, mandame más info."
              className="mt-3 w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
            />
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                disabled={simulateBusy}
                onClick={() => {
                  setSimulateOpen(false)
                  setSimulateMessage('')
                }}
                className="rounded-lg border border-nx-border px-3 py-2 text-sm text-nx-ink hover:bg-nx-card-muted disabled:opacity-50"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={simulateBusy || !simulateMessage.trim()}
                onClick={() => void handleSimulateResponse()}
                className="rounded-lg bg-zinc-700 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
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
