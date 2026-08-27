const CHANNEL_LABELS = {
  email: 'Email',
  linkedin: 'LinkedIn',
  whatsapp: 'WhatsApp',
}

function stepIcon(status) {
  if (status === 'sent' || status === 'respondido') {
    return '✓'
  }
  if (status === 'current') {
    return '●'
  }
  if (status === 'failed') {
    return '!'
  }
  if (status === 'skipped') {
    return '—'
  }
  return '○'
}

function stepClasses(status) {
  if (status === 'sent' || status === 'respondido') {
    return 'border-red-200 bg-red-50 text-red-900'
  }
  if (status === 'current') {
    return 'border-nx-brand/40 bg-nx-brand/10 text-nx-brand ring-2 ring-nx-brand/20'
  }
  if (status === 'failed') {
    return 'border-red-200 bg-red-50 text-red-800'
  }
  if (status === 'skipped') {
    return 'border-nx-border bg-nx-card-muted text-nx-muted line-through'
  }
  return 'border-nx-border bg-white text-nx-subtle'
}

function stepSubtitle(step) {
  if (step.status === 'sent' || step.status === 'respondido') {
    return `Día ${step.day} enviado`
  }
  if (step.status === 'current') {
    return `Día ${step.day} próximo toque`
  }
  if (step.status === 'skipped') {
    return `Día ${step.day} omitido`
  }
  return `Día ${step.day} pendiente`
}

export function SequenceProgressTimeline({ steps = [] }) {
  if (!steps.length) {
    return null
  }

  return (
    <div className="space-y-0">
      {steps.map((step, index) => {
        const channel = CHANNEL_LABELS[step.channel] || step.channel
        return (
          <div key={step.day}>
            <div
              className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${stepClasses(step.status)}`}
            >
              <span className="w-4 shrink-0 text-center font-semibold">{stepIcon(step.status)}</span>
              <div className="min-w-0 flex-1">
                <p className="font-medium">{stepSubtitle(step)}</p>
                <p className="text-xs opacity-80">
                  {channel} · {step.status_label || step.touch_status}
                </p>
              </div>
            </div>
            {index < steps.length - 1 ? (
              <div className="flex justify-center py-0.5 text-nx-border-strong">↓</div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
