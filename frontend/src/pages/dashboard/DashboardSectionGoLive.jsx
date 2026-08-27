import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../../layout/PageHeader'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { useCompany } from '../../context/CompanyContext.jsx'
import { fetchCompanyGoLive, fetchGoogleIntegrationVerify, fetchWhatsAppIntegrationVerify } from '../../utils/api.js'
import { buildGoLiveClientFallback, isGoLiveNotFoundError } from '../../utils/goLiveClient.js'
import { isNexusWhatsAppExtensionReady } from '../../utils/whatsappAssistExtension.js'
import { useAuth } from '../../context/AuthContext.jsx'
import { currentUserId } from '../../utils/campaignUsers.js'

const WORKSPACE_LINKS = {
  product: '/productos',
  product_copy: '/productos',
  credits: '/creditos',
  sdr: '/equipo',
  campaign: '/campanas',
}

function CheckRow({ ok, label, hint, to }) {
  const body = (
    <>
      <span
        className={[
          'mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-bold',
          ok ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900',
        ].join(' ')}
      >
        {ok ? '✓' : '!'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-nx-ink">{label}</p>
        {!ok && hint ? <p className="mt-0.5 text-xs text-nx-muted">{hint}</p> : null}
        {!ok && to ? (
          <p className="mt-1 text-xs font-semibold text-nx-brand">Resolver →</p>
        ) : null}
      </div>
    </>
  )

  if (!ok && to) {
    return (
      <li>
        <Link
          to={to}
          className="flex items-start gap-3 rounded-lg border border-amber-200/90 bg-amber-50/40 px-3 py-2.5 transition hover:border-nx-brand/40 hover:bg-amber-50"
        >
          {body}
        </Link>
      </li>
    )
  }

  return (
    <li className="flex items-start gap-3 rounded-lg border border-nx-border/80 bg-nx-card px-3 py-2.5">
      {body}
    </li>
  )
}

export default function DashboardSectionGoLive() {
  const { companyId } = useCompany()
  const { user } = useAuth()
  const userId = currentUserId(user)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [extOk, setExtOk] = useState(false)
  const [gmailOk, setGmailOk] = useState(false)
  const [waOk, setWaOk] = useState(false)

  const load = useCallback(async () => {
    if (!companyId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetchCompanyGoLive(companyId)
      setData(res)
    } catch (e) {
      if (isGoLiveNotFoundError(e)) {
        try {
          setData(await buildGoLiveClientFallback(companyId))
          return
        } catch (inner) {
          setError(inner instanceof Error ? inner.message : String(inner))
          return
        }
      }
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const ext = isNexusWhatsAppExtensionReady()
    setExtOk(ext)
    if (!companyId || !userId) return
    void (async () => {
      try {
        const v = await fetchGoogleIntegrationVerify(companyId, userId, { deep: false })
        setGmailOk(Boolean(v?.gmail?.connected || v?.google_calendar?.connected))
        const wa = await fetchWhatsAppIntegrationVerify(companyId, userId, { deep: false })
        const assisted = wa?.mode === 'assisted' || (wa?.configured && !wa?.dry_run)
        setWaOk(Boolean(assisted && ext) && !wa?.dry_run)
      } catch {
        setGmailOk(false)
        setWaOk(ext)
      }
    })()
  }, [companyId, userId])

  const sdrChecks = useMemo(
    () => [
      {
        id: 'google',
        ok: gmailOk,
        label: 'Google conectado (Gmail + Calendar)',
        hint: 'Conectá la cuenta Google del SDR.',
        to: '/configuracion/integraciones',
      },
      {
        id: 'ext',
        ok: extOk,
        label: 'Extensión Nexus en Chrome',
        hint: 'Chrome Web Store (WhatsApp). LinkedIn es LI-SAFE desde Nexus.',
        to: '/configuracion/integraciones',
      },
      {
        id: 'wa',
        ok: waOk,
        label: 'WhatsApp Web listo',
        hint: 'Misma extensión + WhatsApp Web abierto en Chrome.',
        to: '/configuracion/integraciones',
      },
    ],
    [gmailOk, extOk, waOk],
  )

  const workspaceChecks = (data?.workspace?.checks || []).map((c) => ({
    ...c,
    to: WORKSPACE_LINKS[c.id],
  }))

  return (
    <>
      <PageHeader
        title="Go-live"
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {loading ? <p className="text-sm text-nx-muted">Evaluando readiness…</p> : null}

      {!loading && data ? (
        <div className="space-y-6">
          <section className="nx-card rounded-2xl border border-nx-border p-5">
            <h2 className="text-sm font-semibold text-nx-ink">Empresa</h2>
            <ul className="mt-3 space-y-2">
              {workspaceChecks.map((c) => (
                <CheckRow key={c.id} ok={c.ok} label={c.label} hint={c.hint} to={c.to} />
              ))}
            </ul>
          </section>

          <section className="nx-card rounded-2xl border border-nx-border p-5">
            <h2 className="text-sm font-semibold text-nx-ink">Integraciones del SDR</h2>
            <ul className="mt-3 space-y-2">
              {sdrChecks.map((c) => (
                <CheckRow key={c.id} ok={c.ok} label={c.label} hint={c.hint} to={c.to} />
              ))}
            </ul>
          </section>

          <section className="nx-card rounded-2xl border border-nx-border p-5">
            <h2 className="text-sm font-semibold text-nx-ink">Kickoff (10 min)</h2>
            <p className="mt-1 text-xs text-nx-muted">
              Call post-pago con el SDR. Script completo: docs/KICKOFF_CLIENTE.md
            </p>
            <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-sm text-nx-ink">
              <li>
                Login con nombre real →{' '}
                <Link to="/mi-perfil" className="font-semibold text-nx-brand">
                  Mi perfil
                </Link>
              </li>
              <li>
                Producto con copy →{' '}
                <Link to="/productos" className="font-semibold text-nx-brand">
                  Productos
                </Link>
              </li>
              <li>
                Gmail + Calendar →{' '}
                <Link to="/configuracion/integraciones" className="font-semibold text-nx-brand">
                  Integraciones
                </Link>
              </li>
              <li>Extensión Chrome Nexus en el mismo perfil</li>
              <li>WhatsApp Web abierto en ese Chrome</li>
              <li>
                Primera campaña LI → Email → WA →{' '}
                <Link to="/campanas" className="font-semibold text-nx-brand">
                  Campañas
                </Link>
              </li>
            </ol>
          </section>
        </div>
      ) : null}
    </>
  )
}
