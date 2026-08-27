import { Modal } from '../Modal.jsx'
import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import { copyTextToClipboard } from '../../utils/linkedinAssist.js'

function phoneKindLabel(kind) {
  if (kind === 'landline') return 'Teléfono fijo'
  if (kind === 'mobile') return 'Celular'
  return 'Teléfono'
}

/**
 * Marcador asistido estilo Outreach: número + guion + abrir marcador del dispositivo.
 * (Outreach usa VoIP propio; acá usamos tel: + copiar — funciona en móvil y softphones.)
 */
export function CallDialerModal({ open, task, busy = false, onClose, onMarkDone }) {
  if (!open || !task) return null

  const name = task.prospect_name || 'Prospecto'
  const company = task.company_name || ''
  const display = task.phone_display || task.phone_digits || '—'
  const brief = (task.brief || '').trim()
  const telHref = task.tel_href || ''

  return (
    <Modal
      title={`Llamar · ${name}`}
      onClose={onClose}
      footer={
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="rounded-lg border border-nx-border px-3 py-1.5 text-xs text-nx-muted"
            onClick={onClose}
          >
            Cerrar
          </button>
          <PremiumGradientButton
            className="px-4 py-2 text-xs"
            disabled={busy}
            loading={busy}
            onClick={() => onMarkDone?.(task)}
          >
            Marcar llamada hecha
          </PremiumGradientButton>
        </div>
      }
    >
      <div className="space-y-4">
        <p className="text-[12px] text-nx-muted">
          Como Outreach: abrís el marcador, hablás con el prospecto y registrás la actividad acá.
          En celular «Llamar ahora» abre tu app de teléfono; en PC puede abrir Teams, Skype o tu
          softphone si tenés <code className="text-[11px]">tel:</code> configurado.
        </p>

        <div className="rounded-xl border border-violet-200 bg-violet-50/80 p-4 text-center">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-violet-800">
            {phoneKindLabel(task.phone_kind)}
          </p>
          <p className="mt-1 text-2xl font-bold tabular-nums tracking-wide text-nx-ink">{display}</p>
          {company ? <p className="mt-1 text-[12px] text-nx-muted">{company}</p> : null}
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {telHref ? (
              <a
                href={telHref}
                className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700"
              >
                <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 002.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 01-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 00-1.091-.852H4.5A2.25 2.25 0 002.25 4.5v2.25z"
                  />
                </svg>
                Llamar ahora
              </a>
            ) : null}
            <button
              type="button"
              className="rounded-xl border border-nx-border bg-white px-4 py-2.5 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
              onClick={() => void copyTextToClipboard(display)}
            >
              Copiar número
            </button>
          </div>
        </div>

        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">Guion del toque</p>
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-nx-border bg-nx-card-muted/60 p-3 text-[12px] leading-relaxed text-nx-ink">
            {brief || 'Sin guion generado.'}
          </pre>
          {brief ? (
            <button
              type="button"
              className="mt-2 text-[11px] font-semibold text-nx-brand hover:underline"
              onClick={() => void copyTextToClipboard(brief)}
            >
              Copiar guion
            </button>
          ) : null}
        </div>
      </div>
    </Modal>
  )
}
