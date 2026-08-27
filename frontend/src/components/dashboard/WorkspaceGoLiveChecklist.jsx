import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'
import { useCompany } from '../../context/CompanyContext.jsx'
import { currentUserId } from '../../utils/campaignUsers.js'
import {
  fetchCampaigns,
  fetchGoogleIntegrationVerify,
  fetchProducts,
  fetchWhatsAppIntegrationVerify,
} from '../../utils/api.js'
import { isNexusWhatsAppExtensionReady } from '../../utils/whatsappAssistExtension.js'
import { useMyCredits } from '../../hooks/useMyCredits.js'

const COLLAPSED_KEY = 'nexus.go_live.collapsed'
const LEGACY_DISMISS_KEY = 'nexus.go_live.dismissed'

function readCollapsed(companyId) {
  try {
    const cid = companyId || 0
    const stored = localStorage.getItem(`${COLLAPSED_KEY}:${cid}`)
    if (stored === '0') return false
    if (stored === '1') return true
    if (localStorage.getItem(`${LEGACY_DISMISS_KEY}:${cid}`) === '1') return true
  } catch {
    /* ignore */
  }
  return true
}

function CheckItem({ ok, label, hint, to }) {
  return (
    <li className="flex items-start gap-2.5 rounded-lg border border-nx-border/70 bg-nx-bg/40 px-3 py-2">
      <span
        className={[
          'mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold',
          ok ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900',
        ].join(' ')}
        aria-hidden
      >
        {ok ? '✓' : '!'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-nx-ink">{label}</p>
        {!ok && hint ? <p className="mt-0.5 text-xs text-nx-muted">{hint}</p> : null}
        {!ok && to ? (
          <Link to={to} className="mt-1 inline-block text-xs font-semibold text-nx-brand hover:underline">
            Configurar →
          </Link>
        ) : null}
      </div>
    </li>
  )
}

/**
 * Checklist de go-live en consola: lo mínimo para que una SDR pueda vender sin soporte.
 */
export function WorkspaceGoLiveChecklist() {
  const { companyId } = useCompany()
  const { user } = useAuth()
  const userId = currentUserId(user)
  const { available: creditsAvailable, showCredits, loading: creditsLoading } = useMyCredits()

  const [collapsed, setCollapsed] = useState(() => readCollapsed(companyId))
  const [loading, setLoading] = useState(true)
  const [gmailOk, setGmailOk] = useState(false)
  const [calendarOk, setCalendarOk] = useState(false)
  const [extOk, setExtOk] = useState(false)
  const [whatsappOk, setWhatsappOk] = useState(false)
  const [hasProduct, setHasProduct] = useState(false)
  const [hasCampaign, setHasCampaign] = useState(false)

  const load = useCallback(async () => {
    if (!companyId || !userId) {
      setLoading(false)
      return
    }
    setLoading(true)
    const extension = isNexusWhatsAppExtensionReady()
    setExtOk(extension)
    try {
      const [verify, products, campaigns] = await Promise.all([
        fetchGoogleIntegrationVerify(companyId, userId, { deep: false }),
        fetchProducts(companyId).catch(() => []),
        fetchCampaigns(companyId).catch(() => []),
      ])
      const gmail = verify?.gmail
      const cal = verify?.google_calendar
      setGmailOk(Boolean(gmail?.connected || gmail?.effective_status === 'connected'))
      setCalendarOk(Boolean(cal?.connected || cal?.effective_status === 'connected'))
      setHasProduct(Array.isArray(products) && products.length > 0)
      setHasCampaign(Array.isArray(campaigns) && campaigns.length > 0)
      try {
        const wa = await fetchWhatsAppIntegrationVerify(companyId, userId, { deep: false })
        const assisted = wa?.mode === 'assisted' || (wa?.configured && !wa?.dry_run)
        setWhatsappOk(Boolean(assisted && extension) && !wa?.dry_run)
      } catch {
        setWhatsappOk(extension)
      }
    } catch {
      setGmailOk(false)
      setCalendarOk(false)
    } finally {
      setLoading(false)
    }
  }, [companyId, userId])

  useEffect(() => {
    void load()
  }, [load])

  const creditsOk = !showCredits || creditsLoading || Number(creditsAvailable) > 0

  const items = useMemo(
    () => [
      {
        id: 'google',
        ok: gmailOk && calendarOk,
        label: 'Google conectado (Gmail + Calendar)',
        hint: 'Necesario para emails automáticos y agendar reuniones.',
        to: '/configuracion/integraciones',
      },
      {
        id: 'extension',
        ok: extOk,
        label: 'Extensión Nexus en Chrome',
        hint: 'WhatsApp Web asistido (Chrome Web Store). LinkedIn va LI-SAFE desde Nexus.',
        to: '/configuracion/integraciones',
      },
      {
        id: 'whatsapp',
        ok: whatsappOk,
        label: 'WhatsApp Web listo',
        hint: 'Extensión activa + WhatsApp Web abierto en el mismo Chrome.',
        to: '/configuracion/integraciones',
      },
      {
        id: 'credits',
        ok: creditsOk,
        label: 'Créditos para prospectar',
        hint: 'Asigná créditos al SDR (mínimo ~30 para una campaña).',
        to: '/creditos',
      },
      {
        id: 'product',
        ok: hasProduct,
        label: 'Producto o servicio cargado',
        hint: 'La IA usa el producto para redactar mensajes.',
        to: '/productos',
      },
      {
        id: 'campaign',
        ok: hasCampaign,
        label: 'Al menos una campaña',
        hint: 'Creá campaña con plantilla LinkedIn → Email → WhatsApp.',
        to: '/campanas',
      },
    ],
    [gmailOk, calendarOk, extOk, whatsappOk, creditsOk, hasProduct, hasCampaign],
  )

  const pending = items.filter((i) => !i.ok)
  const allReady = pending.length === 0

  useEffect(() => {
    setCollapsed(readCollapsed(companyId))
  }, [companyId])

  function persistCollapsed(next) {
    setCollapsed(next)
    try {
      localStorage.setItem(`${COLLAPSED_KEY}:${companyId || 0}`, next ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  if (!companyId) {
    return null
  }

  const summary = loading
    ? 'Revisando si Nexus está listo…'
    : allReady
      ? 'Nexus detecta que está todo listo'
      : `Nexus detecta ${pending.length} pendiente${pending.length === 1 ? '' : 's'}`

  return (
    <section
      className={[
        'rounded-xl border shadow-sm',
        allReady && !loading ? 'border-emerald-200/90 bg-emerald-50/50' : 'border-amber-200/90 bg-amber-50/60',
      ].join(' ')}
    >
      <button
        type="button"
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
        onClick={() => persistCollapsed(!collapsed)}
        aria-expanded={!collapsed}
      >
        <span
          className={[
            'flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold',
            loading
              ? 'bg-slate-200 text-slate-600'
              : allReady
                ? 'bg-emerald-100 text-emerald-800'
                : 'bg-amber-100 text-amber-900',
          ].join(' ')}
          aria-hidden
        >
          {loading ? '…' : allReady ? '✓' : '!'}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-nx-ink">Para que Nexus funcione</span>
          <span className="mt-0.5 block text-xs text-nx-muted">{summary}</span>
        </span>
        <span className="shrink-0 text-xs font-semibold text-nx-brand">
          {collapsed ? 'Ver lista' : 'Plegar'}
        </span>
        <span className="shrink-0 text-nx-muted" aria-hidden>
          {collapsed ? '▸' : '▾'}
        </span>
      </button>
      {!collapsed ? (
        <div className="border-t border-black/5 px-4 py-3">
          <p className="text-xs text-nx-muted">
            {allReady
              ? 'Todo lo mínimo para vender está cubierto. Podés revisar el detalle cuando quieras.'
              : 'Completá lo pendiente antes de una campaña real.'}{' '}
            <Link to="/dashboard/go-live" className="font-semibold text-nx-brand hover:underline">
              Checklist completo →
            </Link>
          </p>
          <ul className="mt-3 space-y-2">
            {items.map((item) => (
              <CheckItem key={item.id} {...item} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  )
}
