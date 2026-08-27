import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CHANNEL_LABELS, CHANNEL_ORDER, DEFAULT_ALLOWED_CHANNELS, orderChannels } from '../../utils/campaignChannels.js'
import { fetchGoogleIntegrationVerify, fetchWhatsAppIntegrationVerify } from '../../utils/api.js'
import { isNexusWhatsAppExtensionReady } from '../../utils/whatsappAssistExtension.js'

function cbClass(checked) {
  return [
    'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors',
    checked
      ? 'border-nx-brand bg-nx-brand/5 text-nx-ink'
      : 'border-nx-border bg-white text-nx-muted hover:border-nx-border-strong',
  ].join(' ')
}

function StatusBadge({ status, channelId }) {
  if (status === 'connected') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold text-red-700">
        <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
        Conectado
      </span>
    )
  }
  if (status === 'missing') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-zinc-50 px-1.5 py-0.5 text-[10px] font-semibold text-zinc-700">
        <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
        Falta conectar
      </span>
    )
  }
  return null
}

/**
 * Muestra los canales que Nexus puede usar según lo que el SDR ya conectó.
 * Los conectados se pueden usar; los que faltan se avisan (se conectan en Integraciones).
 */
export function CampaignChannelsField({ value, onChange, disabled, hintClassName, companyId, sellerId }) {
  const ordered = orderChannels(value?.length ? value : DEFAULT_ALLOWED_CHANNELS)
  const set = new Set(ordered)

  const [status, setStatus] = useState(null)

  const loadStatus = useCallback(async () => {
    if (!companyId || !sellerId) {
      setStatus(null)
      return
    }
    const extOk = isNexusWhatsAppExtensionReady()
    // LinkedIn es LI-SAFE desde Nexus (no requiere scripts LI en la extensión Store).
    const next = { email: 'missing', linkedin: 'connected', whatsapp: 'missing', call: 'connected' }
    try {
      const verify = await fetchGoogleIntegrationVerify(companyId, sellerId, { deep: false })
      const gmail = verify?.gmail
      next.email =
        gmail?.connected || gmail?.effective_status === 'connected' ? 'connected' : 'missing'
    } catch {
      next.email = 'missing'
    }
    try {
      const wa = await fetchWhatsAppIntegrationVerify(companyId, sellerId, { deep: false })
      const assisted = wa?.mode === 'assisted' || (wa?.configured && !wa?.dry_run)
      next.whatsapp = assisted && extOk ? 'connected' : 'missing'
    } catch {
      next.whatsapp = extOk ? 'connected' : 'missing'
    }
    setStatus(next)
  }, [companyId, sellerId])

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

  function toggle(channelId) {
    if (disabled) {
      return
    }
    if (set.has(channelId)) {
      if (ordered.length <= 1) {
        return
      }
      onChange(ordered.filter((c) => c !== channelId))
      return
    }
    // Al habilitar, se agrega al final → ese orden se respeta en la secuencia.
    onChange(orderChannels([...ordered, channelId]))
  }

  const hint = hintClassName ?? 'mt-1 text-[11px] text-nx-subtle'
  const anyMissing = status && ['email', 'linkedin', 'whatsapp'].some((id) => status[id] === 'missing')

  return (
    <div>
      <p className="text-xs font-medium text-nx-ink">Canales conectados</p>
      <p className={hint}>
        Email (Gmail), LinkedIn asistido, WhatsApp Web (extensión) y Llamada (tu teléfono / softphone).
        Si falta conectar alguno, los toques de ese canal se omiten hasta que esté listo.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {CHANNEL_ORDER.map((id) => {
          const checked = set.has(id)
          const chStatus = status?.[id] ?? null
          return (
            <label key={id} className={cbClass(checked)}>
              <input
                type="checkbox"
                className="h-3.5 w-3.5 rounded border-nx-border-strong text-nx-brand focus:ring-nx-brand/25"
                checked={checked}
                disabled={disabled || (checked && set.size <= 1)}
                onChange={() => toggle(id)}
              />
              <span>{CHANNEL_LABELS[id]}</span>
              <StatusBadge status={chStatus} channelId={id} />
            </label>
          )
        })}
      </div>
      {anyMissing ? (
        <p className="mt-2 text-[11px] text-zinc-700">
          Hay canales sin conectar.{' '}
          <Link
            to="/configuracion/integraciones"
            className="font-semibold underline hover:text-zinc-800"
          >
            Conectarlos en Configuración →
          </Link>
        </p>
      ) : null}
    </div>
  )
}
