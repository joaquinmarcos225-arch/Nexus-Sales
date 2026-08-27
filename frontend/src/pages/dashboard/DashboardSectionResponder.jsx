import { Link } from 'react-router-dom'
import { useCallback, useEffect, useState } from 'react'
import { PageHeader } from '../../layout/PageHeader'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { useCompany } from '../../context/CompanyContext.jsx'
import { fetchResponderInbox } from '../../utils/api.js'
import { notifyResponderInboxChanged } from '../../hooks/useResponderPending.js'

const CHANNEL_LABELS = {
  linkedin: 'LinkedIn',
  whatsapp: 'WhatsApp',
  email: 'Email',
}

const CHANNEL_STYLE = {
  linkedin: 'bg-[#0A66C2]/10 text-[#0A66C2] ring-[#0A66C2]/25',
  whatsapp: 'bg-[#25D366]/15 text-[#075E54] ring-[#25D366]/30',
  email: 'bg-red-50 text-red-800 ring-red-200/80',
}

function fmtDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return '—'
  }
}

export default function DashboardSectionResponder() {
  const { companyId } = useCompany()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!companyId) {
      setData(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetchResponderInbox(companyId)
      setData(res)
      notifyResponderInboxChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void load()
  }, [load])

  const items = Array.isArray(data?.items) ? data.items : []
  const byChannel = data?.by_channel || {}

  return (
    <>
      <PageHeader
        title="Responder"
        description="Conversaciones con borrador listo: email, LinkedIn y WhatsApp Web."
        actions={
          <button
            type="button"
            className="rounded-lg border border-nx-border px-3 py-1.5 text-xs font-semibold hover:bg-nx-card-muted"
            onClick={() => void load()}
          >
            Actualizar
          </button>
        }
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <div className="mb-4 flex flex-wrap gap-2 text-[11px]">
        {(['linkedin', 'whatsapp', 'email']).map((ch) => (
          <span
            key={ch}
            className={`rounded-full px-2.5 py-1 font-semibold ring-1 ${CHANNEL_STYLE[ch]}`}
          >
            {CHANNEL_LABELS[ch]}: {byChannel[ch] ?? 0}
          </span>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando bandeja…</p>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-nx-border bg-nx-card px-4 py-10 text-center text-sm text-nx-muted">
          No hay respuestas pendientes. Cuando un prospecto responda, el borrador aparece acá.
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => {
            const ch = item.channel || 'email'
            return (
              <li
                key={`${item.prospect_id}-${ch}`}
                className="rounded-xl border border-nx-border bg-white p-4 shadow-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ${CHANNEL_STYLE[ch] || ''}`}
                      >
                        {CHANNEL_LABELS[ch] || ch}
                      </span>
                      <p className="text-sm font-semibold text-nx-ink">{item.prospect_name}</p>
                      <span className="text-xs text-nx-muted">· {item.company_name}</span>
                    </div>
                    <p className="mt-1 text-[11px] text-nx-muted">
                      {item.campaign_name} · inbound {fmtDate(item.last_inbound_at)}
                    </p>
                  </div>
                  <Link
                    to={item.focus_url || `/campanas/${item.campaign_id}`}
                    className="shrink-0 rounded-lg bg-nx-ink px-3 py-2 text-xs font-semibold text-white hover:opacity-90"
                  >
                    Abrir en campaña
                  </Link>
                </div>
                {item.inbound_preview ? (
                  <blockquote className="mt-3 rounded-lg border-l-4 border-nx-brand/40 bg-nx-bg/60 px-3 py-2 text-xs leading-relaxed text-nx-ink">
                    {item.inbound_preview}
                  </blockquote>
                ) : null}
                {item.draft ? (
                  <div className="mt-3 rounded-lg border border-nx-border bg-nx-card-muted/40 p-3">
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-nx-muted">
                      Borrador sugerido
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-nx-ink">{item.draft}</p>
                  </div>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}
