import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchGoogleIntegrationVerify, fetchWhatsAppIntegrationVerify } from '../../utils/api.js'
import { isNexusWhatsAppExtensionReady } from '../../utils/whatsappAssistExtension.js'
import { orderChannels } from '../../utils/campaignChannels.js'

function IntegrationRow({ name, purpose, ok, detail }) {
  if (ok) {
    return null
  }
  return (
    <div className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-zinc-200/80 bg-zinc-50/50 px-3 py-2.5">
      <div className="min-w-0">
        <p className="text-xs font-semibold text-nx-ink">{name}</p>
        <p className="mt-0.5 text-[11px] leading-relaxed text-nx-muted">{purpose}</p>
        {detail ? <p className="mt-1 text-[11px] text-zinc-900/90">{detail}</p> : null}
      </div>
    </div>
  )
}

/**
 * Avisos solo cuando falta algo del vendedor (vista SDR). Sin APIs de backend ni botón de iniciar.
 */
export function CampaignSetupChecklist({ campaign, companyId, sequenceRunning = false }) {
  const [loading, setLoading] = useState(true)
  const [gmailOk, setGmailOk] = useState(false)
  const [calendarOk, setCalendarOk] = useState(false)
  const [extensionOk, setExtensionOk] = useState(false)
  const [whatsappOk, setWhatsappOk] = useState(false)
  const [whatsappDryRun, setWhatsappDryRun] = useState(false)

  const sellerOk = Boolean(campaign?.seller_id)
  const whatsappRequired = orderChannels(campaign?.allowed_channels).includes('whatsapp')

  const load = useCallback(async () => {
    setLoading(true)
    const extOk = isNexusWhatsAppExtensionReady()
    setExtensionOk(extOk)

    if (companyId && campaign?.seller_id) {
      try {
        const verify = await fetchGoogleIntegrationVerify(companyId, campaign.seller_id, { deep: false })
        const gmail = verify?.gmail
        const cal = verify?.google_calendar
        setGmailOk(Boolean(gmail?.connected || gmail?.effective_status === 'connected'))
        setCalendarOk(Boolean(cal?.connected || cal?.effective_status === 'connected'))
        try {
          const wa = await fetchWhatsAppIntegrationVerify(companyId, campaign.seller_id, { deep: false })
          const assisted = wa?.mode === 'assisted' || (wa?.configured && !wa?.dry_run)
          setWhatsappDryRun(Boolean(wa?.dry_run))
          setWhatsappOk(Boolean(assisted && extOk) && !wa?.dry_run)
        } catch {
          setWhatsappOk(extOk)
          setWhatsappDryRun(false)
        }
      } catch {
        setGmailOk(false)
        setCalendarOk(false)
      }
    } else {
      setGmailOk(false)
      setCalendarOk(false)
      setWhatsappOk(extOk)
    }
    setLoading(false)
  }, [companyId, campaign?.seller_id])

  useEffect(() => {
    void load()
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        void load()
      }
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [load])

  if (loading) {
    return null
  }

  // LinkedIn es LI-SAFE (sin scripts LI en Store). Extensión = WhatsApp Web asistido.
  const allOk =
    sellerOk && gmailOk && calendarOk && (!whatsappRequired || (whatsappOk && extensionOk))
  if (allOk) {
    return null
  }

  return (
    <section className="rounded-xl border border-zinc-200/90 bg-zinc-50/40 px-4 py-3 shadow-sm">
      <p className="text-sm font-semibold text-nx-ink">
        {sequenceRunning ? 'Pendiente para que la secuencia avance' : 'Conexiones recomendadas'}
      </p>
      <p className="mt-0.5 text-xs text-nx-muted">
        {sequenceRunning
          ? 'La secuencia está en marcha pero falta esto para contactar prospectos.'
          : 'Para que la secuencia funcione sin fricción, conectá lo que falte abajo.'}
      </p>
      <div className="mt-3 space-y-2">
        {!sellerOk ? (
          <IntegrationRow
            name="Vendedor asignado"
            purpose="La campaña necesita un SDR/AE responsable."
            ok={false}
            detail="Editá la campaña y elegí quién opera esta secuencia."
          />
        ) : null}
        <IntegrationRow
          name="Gmail"
          purpose="Envío de emails y lectura de respuestas."
          ok={gmailOk}
          detail="Conectá tu cuenta en Integraciones."
        />
        <IntegrationRow
          name="Google Calendar"
          purpose="Detectar reuniones agendadas."
          ok={calendarOk}
          detail="Se conecta junto con Google en Integraciones."
        />
        {whatsappRequired ? (
          <>
            <IntegrationRow
              name="Extensión Nexus (Chrome)"
              purpose="WhatsApp Web asistido desde Chrome Web Store."
              ok={extensionOk}
              detail="Instalá la extensión Chrome desde Integraciones."
            />
            <IntegrationRow
              name="WhatsApp Web"
              purpose="Toques por WhatsApp Web asistido."
              ok={whatsappOk}
              detail={
                whatsappDryRun
                  ? 'WHATSAPP_DRY_RUN sigue activo en el servidor — desactivalo.'
                  : 'Con la extensión instalada, abrí WhatsApp Web en este Chrome.'
              }
            />
          </>
        ) : null}
      </div>
      <Link
        to="/configuracion/integraciones"
        className="mt-3 inline-block text-xs font-semibold text-red-700 hover:text-red-800"
      >
        Ir a configuración →
      </Link>
    </section>
  )
}
