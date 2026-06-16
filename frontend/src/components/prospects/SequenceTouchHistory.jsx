import { useState } from 'react'
import { SdrValidationDebugPanel } from '../outreach/SdrValidationDebugPanel.jsx'
import { fmtDate, fmtDateTime, fmtTime } from '../../utils/ownershipUi.js'

const CHANNEL_LABELS = {
  email: 'Email',
  linkedin: 'LinkedIn',
  whatsapp: 'WhatsApp',
}

function statusBadgeClass(status, touchStatus) {
  if (status === 'sent' || touchStatus === 'enviado') {
    return 'bg-emerald-50 text-emerald-800 ring-emerald-600/20'
  }
  if (status === 'respondido' || touchStatus === 'respondido') {
    return 'bg-violet-50 text-violet-800 ring-violet-600/20'
  }
  if (status === 'current') {
    return 'bg-nx-brand/10 text-nx-brand ring-nx-brand/30'
  }
  if (status === 'failed' || touchStatus === 'fallido') {
    return 'bg-red-50 text-red-800 ring-red-600/20'
  }
  if (status === 'skipped' || touchStatus === 'omitido') {
    return 'bg-slate-100 text-slate-600 ring-slate-500/20'
  }
  if (touchStatus === 'generado') {
    return 'bg-sky-50 text-sky-800 ring-sky-600/20'
  }
  return 'bg-slate-100 text-slate-600 ring-slate-500/20'
}

function formatOpenAiError(meta) {
  if (!meta) {
    return null
  }
  const parts = []
  if (meta.model) {
    parts.push(`Modelo: ${meta.model}`)
  }
  if (meta.attempts) {
    parts.push(`${meta.attempts} intentos`)
  }
  if (meta.timestamp) {
    parts.push(meta.timestamp)
  }
  return parts.length ? parts.join(' · ') : null
}

function StepRow({ step, busy, generationStatus, openaiRetryDay, onExecute, onSkip }) {
  const [open, setOpen] = useState(false)
  const channel = CHANNEL_LABELS[step.channel] || step.channel

  return (
    <div className="rounded-lg border border-[#e5e7eb] bg-white p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium text-[#111827]">
            Día {step.day} · {channel}
          </p>
          {step.sent_at ? (
            <>
              <p className="text-xs text-[#6b7280]">Fecha envío: {fmtDate(step.sent_at)}</p>
              <p className="text-xs text-[#6b7280]">Hora envío: {fmtTime(step.sent_at)}</p>
            </>
          ) : step.scheduled_at ? (
            <p className="text-xs text-[#6b7280]">Programado: {fmtDateTime(step.scheduled_at)}</p>
          ) : null}
          {step.fallback_test ? (
            <p className="mt-1 inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-900 ring-1 ring-amber-400/50">
              FALLBACK TEST
            </p>
          ) : null}
          {step.error_message ? (
            <p
              className={`mt-1 text-xs ${
                step.openai_last_error?.retryable ? 'text-amber-800' : 'text-red-700'
              }`}
            >
              {step.error_message}
            </p>
          ) : null}
          {step.openai_last_error ? (
            <p className="mt-1 text-[10px] text-[#6b7280]">
              {formatOpenAiError(step.openai_last_error)}
            </p>
          ) : null}
          {step.validation_rejection ? (
            <p className="mt-1 text-xs font-medium text-red-800">
              Ver depuración del borrador rechazado abajo
            </p>
          ) : step.touch_status === 'fallido' && !step.validation_rejection ? (
            <p className="mt-1 text-xs text-amber-800">
              Sin borrador guardado — reejecutá el toque para capturar la salida de la IA.
            </p>
          ) : null}
        </div>
        <span
          className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${statusBadgeClass(step.status, step.touch_status)}`}
        >
          {step.status_label || step.touch_status}
        </span>
      </div>

          {step.subject ? (
            <p className="text-xs text-[#6b7280]">Asunto: {step.subject}</p>
          ) : null}
          {step.body || step.message_body ? (
            <div className="mt-2">
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="text-xs font-medium text-nx-brand hover:underline"
              >
                {open ? 'Ocultar mensaje' : 'Ver mensaje'}
              </button>
              {open ? (
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-[#f8fafc] p-3 text-xs text-[#374151]">
                  {step.body || step.message_body}
                </pre>
              ) : null}
            </div>
          ) : step.touch_status === 'enviado' || step.touch_status === 'respondido' ? (
            <p className="mt-2 text-xs text-amber-700">Mensaje no disponible — reejecutá el toque si falló antes.</p>
          ) : null}

      {step.validation_rejection ? (
        <div className="mt-3">
          <SdrValidationDebugPanel validation={step.validation_rejection} compact />
        </div>
      ) : null}

      {step.can_execute ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onExecute?.(step.day)}
            className="rounded-lg bg-nx-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-nx-brand/90 disabled:opacity-50"
          >
            {busy
              ? generationStatus || 'Generando…'
              : step.openai_last_error?.retryable || openaiRetryDay === step.day
                ? 'Reintentar toque'
                : 'Ejecutar toque'}
          </button>
          {step.can_skip ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => onSkip?.(step.day)}
              className="rounded-lg border border-[#e5e7eb] px-3 py-1.5 text-xs font-medium text-[#374151] hover:bg-[#f8fafc] disabled:opacity-50"
            >
              Omitir
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function SequenceTouchHistory({
  steps = [],
  history = [],
  title = 'Toques de secuencia',
  busyDay = null,
  generationStatus = null,
  openaiRetryDay = null,
  onExecute,
  onSkip,
}) {
  const rows = steps.length ? steps : history

  if (!rows.length) {
    return (
      <p className="text-xs text-[#6b7280]">Todavía no hay toques registrados en esta secuencia.</p>
    )
  }

  return (
    <div className="space-y-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6b7280]">{title}</h4>
      {rows.map((step) => (
        <StepRow
          key={step.day}
          step={step}
          busy={busyDay === step.day}
          generationStatus={generationStatus}
          openaiRetryDay={openaiRetryDay}
          onExecute={onExecute}
          onSkip={onSkip}
        />
      ))}
    </div>
  )
}
