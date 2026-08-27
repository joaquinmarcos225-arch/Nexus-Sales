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
    return 'bg-red-50 text-red-800 ring-red-600/20'
  }
  if (status === 'respondido' || touchStatus === 'respondido') {
    return 'bg-zinc-50 text-zinc-800 ring-zinc-600/20'
  }
  if (status === 'current') {
    return 'bg-nx-brand/10 text-nx-brand ring-nx-brand/30'
  }
  if (status === 'failed' || touchStatus === 'fallido') {
    return 'bg-red-50 text-red-800 ring-red-600/20'
  }
  if (status === 'skipped' || touchStatus === 'omitido') {
    return 'bg-nx-card-muted text-nx-muted ring-nx-muted/20'
  }
  if (touchStatus === 'generado') {
    return 'bg-zinc-50 text-zinc-800 ring-zinc-600/20'
  }
  return 'bg-nx-card-muted text-nx-muted ring-nx-muted/20'
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

function StepRow({ step, busy, generationStatus, openaiRetryDay, hideErrors, onExecute, onSkip, onMarkSent }) {
  const [open, setOpen] = useState(false)
  const channel = CHANNEL_LABELS[step.channel] || step.channel
  const showErrors = !hideErrors

  return (
    <div className="rounded-lg border border-nx-border bg-white p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium text-nx-ink">
            Día {step.day} · {channel}
          </p>
          {step.sent_at ? (
            <>
              <p className="text-xs text-nx-muted">Fecha envío: {fmtDate(step.sent_at)}</p>
              <p className="text-xs text-nx-muted">Hora envío: {fmtTime(step.sent_at)}</p>
            </>
          ) : step.scheduled_at ? (
            <p className="text-xs text-nx-muted">Programado: {fmtDateTime(step.scheduled_at)}</p>
          ) : null}
          {step.fallback_test ? (
            <p className="mt-1 inline-flex rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-zinc-900 ring-1 ring-zinc-400/50">
              FALLBACK TEST
            </p>
          ) : null}
          {showErrors && step.error_message ? (
            <p
              className={`mt-1 text-xs ${
                step.openai_last_error?.retryable ? 'text-zinc-800' : 'text-red-700'
              }`}
            >
              {step.error_message}
            </p>
          ) : null}
          {showErrors && step.openai_last_error ? (
            <p className="mt-1 text-[10px] text-nx-muted">
              {formatOpenAiError(step.openai_last_error)}
            </p>
          ) : null}
          {showErrors && step.validation_rejection ? (
            <p className="mt-1 text-xs font-medium text-red-800">
              Ver depuración del borrador rechazado abajo
            </p>
          ) : showErrors && step.touch_status === 'fallido' && !step.validation_rejection ? (
            <p className="mt-1 text-xs text-zinc-800">
              {step.error_message ||
                'El intento anterior falló — usá Ejecutar toque para generar y enviar de nuevo.'}
            </p>
          ) : step.can_execute && step.touch_status === 'pendiente' ? (
            <p className="mt-1 text-xs text-nx-muted">
              Próximo toque — Ejecutar genera el mensaje con IA y prepara el envío.
            </p>
          ) : step.can_mark_sent ? (
            <p className="mt-1 text-xs text-nx-muted">
              Borrador en Gmail listo. Enviá desde tu cuenta y marcá como enviado acá.
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
            <p className="text-xs text-nx-muted">Asunto: {step.subject}</p>
          ) : null}
          {step.gmail_web_link ? (
            <a
              href={step.gmail_web_link}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-block text-xs font-medium text-nx-brand hover:underline"
            >
              Abrir borrador en Gmail
            </a>
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
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-nx-card-muted p-3 text-xs text-nx-ink">
                  {step.body || step.message_body}
                </pre>
              ) : null}
            </div>
          ) : step.touch_status === 'enviado' || step.touch_status === 'respondido' ? (
            <p className="mt-2 text-xs text-zinc-700">Mensaje no disponible — reejecutá el toque si falló antes.</p>
          ) : null}

      {showErrors && step.validation_rejection ? (
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
            className="nx-btn nx-btn-primary px-3 py-1.5 text-xs"
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
              className="rounded-lg border border-nx-border px-3 py-1.5 text-xs font-medium text-nx-ink hover:bg-nx-card-muted disabled:opacity-50"
            >
              Omitir
            </button>
          ) : null}
        </div>
      ) : null}

      {step.can_mark_sent ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onMarkSent?.(step.day)}
            className="rounded-lg bg-red-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-800 disabled:opacity-50"
          >
            {busy ? 'Guardando…' : 'Marcar como enviado'}
          </button>
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
  onMarkSent,
}) {
  const rows = steps.length ? steps : history

  if (!rows.length) {
    return (
      <p className="text-xs text-nx-muted">Todavía no hay toques registrados en esta secuencia.</p>
    )
  }

  return (
    <div className="space-y-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-nx-muted">{title}</h4>
      {rows.map((step) => (
        <StepRow
          key={step.day}
          step={step}
          busy={busyDay === step.day}
          generationStatus={generationStatus}
          openaiRetryDay={openaiRetryDay}
          hideErrors={busyDay === step.day}
          onExecute={onExecute}
          onSkip={onSkip}
          onMarkSent={onMarkSent}
        />
      ))}
    </div>
  )
}
