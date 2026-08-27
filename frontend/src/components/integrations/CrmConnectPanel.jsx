import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  disconnectHubSpotIntegration,
  disconnectSalesforceIntegration,
  fetchHubSpotIntegrationVerify,
  fetchHubSpotOAuthStartUrl,
  fetchSalesforceIntegrationVerify,
  fetchSalesforceOAuthStartUrl,
} from '../../utils/api.js'
import { crmEffectiveStatus } from './integrationUi.jsx'

function statusLabel(effective) {
  if (effective === 'functional') return { text: 'Conectado', className: 'bg-emerald-50 text-emerald-800' }
  if (effective === 'reconnect_required') return { text: 'Reconectar', className: 'bg-amber-50 text-amber-900' }
  return { text: 'No conectado', className: 'bg-zinc-100 text-zinc-700' }
}

function CrmRow({
  title,
  hint,
  verify,
  verifying,
  busy,
  onConnect,
  onDisconnect,
}) {
  const effective = crmEffectiveStatus(verify)
  const pill = statusLabel(effective)
  const account = verify?.portal_name || verify?.org_name || verify?.instance_url || verify?.portal_id || null
  const oauthReady = Boolean(verify?.oauth_configured)
  const connected = effective === 'functional' || effective === 'reconnect_required'

  return (
    <div className="flex flex-col gap-3 border-b border-nx-border px-4 py-4 last:border-b-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-nx-ink">{title}</h3>
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${pill.className}`}>{pill.text}</span>
        </div>
        <p className="mt-1 text-xs text-nx-muted">{hint}</p>
        {account ? <p className="mt-1 text-xs text-nx-ink">{account}</p> : null}
        {verify?.verification_summary ? (
          <p className="mt-1 text-[11px] text-nx-muted">{verify.verification_summary}</p>
        ) : null}
        {verify?.api_error ? <p className="mt-1 text-[11px] font-medium text-red-700">{verify.api_error}</p> : null}
        {!oauthReady && !connected ? (
          <p className="mt-1 text-[11px] text-amber-800">
            Falta configurar OAuth en el servidor (CLIENT_ID / SECRET / REDIRECT_URI).
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || verifying || (!oauthReady && !connected)}
          onClick={() => void onConnect()}
          className="rounded-lg bg-nx-ink px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-40"
        >
          {busy === 'connect' ? 'Abriendo…' : connected ? 'Reconectar' : 'Conectar'}
        </button>
        {connected ? (
          <button
            type="button"
            disabled={busy === 'disconnect'}
            onClick={() => void onDisconnect()}
            className="rounded-lg border border-nx-border px-3 py-2 text-xs font-semibold text-nx-ink hover:bg-nx-card-muted disabled:opacity-40"
          >
            {busy === 'disconnect' ? '…' : 'Desconectar'}
          </button>
        ) : null}
      </div>
    </div>
  )
}

/**
 * HubSpot / Salesforce de la empresa (OAuth). Solo gerente/owner.
 */
export function CrmConnectPanel({ companyId, userId }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [hs, setHs] = useState(null)
  const [sf, setSf] = useState(null)
  const [verifying, setVerifying] = useState(false)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [banner, setBanner] = useState(null)

  useEffect(() => {
    const hsOk = searchParams.get('hubspot')
    const sfOk = searchParams.get('salesforce')
    const hsErr = searchParams.get('hubspot_error')
    const sfErr = searchParams.get('salesforce_error')
    const msg = searchParams.get('msg') || ''
    if (hsOk === 'connected' || sfOk === 'connected') {
      setBanner({
        type: 'ok',
        text: hsOk === 'connected' ? 'HubSpot quedó vinculado.' : 'Salesforce quedó vinculado.',
      })
    } else if (hsErr || sfErr) {
      const decoded = msg ? decodeURIComponent(msg) : ''
      setBanner({
        type: 'err',
        text: `${hsErr ? 'HubSpot' : 'Salesforce'}: ${hsErr || sfErr}${decoded ? ` — ${decoded}` : ''}`,
      })
    } else {
      return
    }
    const next = new URLSearchParams(searchParams)
    next.delete('hubspot')
    next.delete('salesforce')
    next.delete('hubspot_error')
    next.delete('salesforce_error')
    next.delete('msg')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const refresh = useCallback(async () => {
    if (!companyId) {
      setHs(null)
      setSf(null)
      return
    }
    setVerifying(true)
    setError(null)
    try {
      const [h, s] = await Promise.all([
        fetchHubSpotIntegrationVerify(companyId, { deep: true }),
        fetchSalesforceIntegrationVerify(companyId, { deep: true }),
      ])
      setHs(h)
      setSf(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setVerifying(false)
    }
  }, [companyId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function connectHubspot() {
    if (!companyId || userId == null) return
    setBusy('hs-connect')
    setError(null)
    try {
      const res = await fetchHubSpotOAuthStartUrl(companyId, userId)
      const url = res?.authorization_url
      if (!url) throw new Error('No se obtuvo la URL de HubSpot.')
      window.location.assign(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(null)
    }
  }

  async function connectSalesforce() {
    if (!companyId || userId == null) return
    setBusy('sf-connect')
    setError(null)
    try {
      const res = await fetchSalesforceOAuthStartUrl(companyId, userId)
      const url = res?.authorization_url
      if (!url) throw new Error('No se obtuvo la URL de Salesforce.')
      window.location.assign(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(null)
    }
  }

  async function disconnectHubspot() {
    if (!companyId) return
    setBusy('hs-disconnect')
    setError(null)
    try {
      await disconnectHubSpotIntegration(companyId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function disconnectSalesforce() {
    if (!companyId) return
    setBusy('sf-disconnect')
    setError(null)
    try {
      await disconnectSalesforceIntegration(companyId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="nx-card mt-6 overflow-hidden rounded-2xl border border-nx-border">
      <div className="border-b border-nx-border px-4 py-3">
        <h2 className="text-sm font-semibold text-nx-ink">CRM del cliente</h2>
        <p className="mt-0.5 text-xs text-nx-muted">
          HubSpot o Salesforce de la empresa. Nexus no es un CRM: acá se sincronizan exclusiones y no se vuelve a
          prospectar lo que ya tienen.
        </p>
      </div>
      {banner?.type === 'ok' ? (
        <p className="border-b border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-950">{banner.text}</p>
      ) : null}
      {banner?.type === 'err' ? (
        <p className="border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-950">{banner.text}</p>
      ) : null}
      {error ? <p className="border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-950">{error}</p> : null}
      <CrmRow
        title="HubSpot"
        hint="OAuth del portal del cliente. Credenciales Nexus en el servidor."
        verify={hs}
        verifying={verifying}
        busy={busy?.startsWith('hs') ? (busy.endsWith('disconnect') ? 'disconnect' : 'connect') : null}
        onConnect={connectHubspot}
        onDisconnect={disconnectHubspot}
      />
      <CrmRow
        title="Salesforce"
        hint="OAuth de la org del cliente."
        verify={sf}
        verifying={verifying}
        busy={busy?.startsWith('sf') ? (busy.endsWith('disconnect') ? 'disconnect' : 'connect') : null}
        onConnect={connectSalesforce}
        onDisconnect={disconnectSalesforce}
      />
    </section>
  )
}
