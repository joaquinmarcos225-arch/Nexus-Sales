import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import {
  disconnectUserProvider,
  fetchGoogleIntegrationVerify,
  fetchGoogleOAuthStartUrl,
  fetchUserConnections,
} from '../utils/api.js'
import { isNexusWhatsAppExtensionReady } from '../utils/whatsappAssistExtension.js'
import { ExtensionInstallPanel } from '../components/integrations/ExtensionInstallPanel.jsx'
import { GoogleConnectPanel } from '../components/integrations/GoogleConnectPanel.jsx'

function resolveEffectiveStatus(cardRow, verifyRow, { verifying = false } = {}) {
  if (verifyRow?.effective_status) return verifyRow.effective_status
  const stored = String(cardRow?.status || 'not_connected').toLowerCase()
  if (stored === 'connected' || (stored === 'error' && verifyRow?.has_refresh_token)) {
    if (!verifyRow) {
      return verifying ? 'pending' : 'functional'
    }
    if (verifyRow?.requires_reconnect) {
      return 'reconnect_required'
    }
    if (verifyRow?.api_reachable) return 'functional'
    if (verifyRow?.has_refresh_token) return verifying ? 'pending' : 'functional'
    return 'reconnect_required'
  }
  if (stored === 'error') return 'reconnect_required'
  return 'not_connected'
}

export default function IntegracionesPage() {
  const { user } = useAuth()
  const { companyId } = useCompany()
  const [searchParams, setSearchParams] = useSearchParams()
  const userId = user?.user_id ?? user?.id ?? null
  const [cards, setCards] = useState([])
  const [verify, setVerify] = useState(null)
  const [loadingCards, setLoadingCards] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState(null)
  const [oauthBanner, setOauthBanner] = useState(null)
  const [runDeepVerifyAfterOAuth, setRunDeepVerifyAfterOAuth] = useState(false)
  const [extensionDetected, setExtensionDetected] = useState(false)
  const [googleBusy, setGoogleBusy] = useState(false)

  useEffect(() => {
    const ok = searchParams.get('google')
    const err = searchParams.get('google_error')
    const msg = searchParams.get('msg') || ''
    if (ok === 'connected') {
      setOauthBanner({
        type: 'ok',
        text: 'Google quedó vinculado correctamente.',
      })
      setRunDeepVerifyAfterOAuth(true)
      const next = new URLSearchParams(searchParams)
      next.delete('google')
      setSearchParams(next, { replace: true })
    } else if (err) {
      const decoded = msg ? decodeURIComponent(msg) : ''
      setOauthBanner({
        type: 'err',
        text: `OAuth Google: ${err}${decoded ? ` — ${decoded}` : ''}`,
      })
      const next = new URLSearchParams(searchParams)
      next.delete('google_error')
      next.delete('msg')
      setSearchParams(next, { replace: true })
    }
  }, [searchParams, setSearchParams])

  const refreshCards = useCallback(async () => {
    if (!companyId || userId == null) {
      setCards([])
      setVerify(null)
      return
    }
    setLoadingCards(true)
    setError(null)
    try {
      const data = await fetchUserConnections(companyId, userId)
      setCards(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setCards([])
    } finally {
      setLoadingCards(false)
    }
  }, [companyId, userId])

  const runVerify = useCallback(
    async ({ deep = false } = {}) => {
      if (!companyId || userId == null) {
        setVerify(null)
        return
      }
      setVerifying(true)
      try {
        const googleRes = await fetchGoogleIntegrationVerify(companyId, userId, { deep })
        setVerify(googleRes)
        if (deep) await refreshCards()
      } catch (e) {
        setVerify(null)
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setVerifying(false)
      }
    },
    [companyId, userId, refreshCards],
  )

  useEffect(() => {
    void refreshCards()
  }, [refreshCards])

  useEffect(() => {
    void runVerify({ deep: true })
  }, [runVerify])

  useEffect(() => {
    if (!runDeepVerifyAfterOAuth || !companyId || userId == null) return
    setRunDeepVerifyAfterOAuth(false)
    void (async () => {
      await refreshCards()
      await runVerify({ deep: true })
    })()
  }, [runDeepVerifyAfterOAuth, companyId, userId, refreshCards, runVerify])

  const byProvider = useMemo(() => {
    const m = new Map()
    for (const c of cards) {
      m.set(String(c.provider).toLowerCase(), c)
    }
    return m
  }, [cards])

  const gmailRow = byProvider.get('gmail')
  const calRow = byProvider.get('google_calendar')
  const gmailVerify = verify?.gmail
  const calVerify = verify?.google_calendar

  const calEffective =
    verifying && !calVerify && String(calRow?.status || '').toLowerCase() === 'connected'
      ? 'pending'
      : resolveEffectiveStatus(calRow, calVerify, { verifying })
  const gmailEffective =
    verifying && !gmailVerify && String(gmailRow?.status || '').toLowerCase() === 'connected'
      ? 'pending'
      : resolveEffectiveStatus(gmailRow, gmailVerify, { verifying })

  useEffect(() => {
    const tick = () => setExtensionDetected(isNexusWhatsAppExtensionReady())
    tick()
    const id = window.setInterval(tick, 1500)
    const onVis = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [])

  const googleNeedsReconnect =
    gmailEffective === 'reconnect_required' ||
    calEffective === 'reconnect_required' ||
    Boolean(gmailVerify?.requires_reconnect) ||
    Boolean(calVerify?.requires_reconnect)

  const googleConnected =
    !googleNeedsReconnect &&
    gmailEffective !== 'not_connected' &&
    calEffective !== 'not_connected'

  const googleAccount = gmailVerify?.external_email || calVerify?.external_email || gmailRow?.external_email || calRow?.external_email

  async function handleReconnectGoogle() {
    if (!companyId || userId == null || googleBusy) return
    setGoogleBusy(true)
    setError(null)
    try {
      const res = await fetchGoogleOAuthStartUrl(companyId, userId)
      const url = res?.authorization_url
      if (!url) throw new Error('No se obtuvo la URL de Google.')
      window.location.assign(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setGoogleBusy(false)
    }
  }

  async function handleDisconnectGoogle() {
    if (!companyId || userId == null || googleBusy) return
    if (!window.confirm('¿Desconectar Google (Gmail y Calendar) de este usuario?')) return
    setGoogleBusy(true)
    setError(null)
    try {
      const data = await disconnectUserProvider(companyId, userId, 'gmail')
      setCards(Array.isArray(data) ? data : [])
      setVerify(null)
      setOauthBanner({ type: 'ok', text: 'Google quedó desconectado.' })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setGoogleBusy(false)
    }
  }

  return (
    <>
      {oauthBanner?.type === 'ok' ? (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
          {oauthBanner.text}
        </div>
      ) : null}
      {oauthBanner?.type === 'err' ? (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-950">
          {oauthBanner.text}
        </div>
      ) : null}

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {!companyId || userId == null ? (
        <p className="rounded-lg border border-dashed border-nx-border bg-nx-card p-4 text-sm text-nx-muted">
          Iniciá sesión y seleccioná una empresa para ver el estado de los canales.
        </p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <ExtensionInstallPanel detected={extensionDetected} />
          <GoogleConnectPanel
            connected={googleConnected}
            needsReconnect={googleNeedsReconnect}
            account={googleAccount}
            busy={googleBusy}
            verifying={verifying}
            onConnect={handleReconnectGoogle}
            onDisconnect={handleDisconnectGoogle}
          />
          {loadingCards ? (
            <p className="text-xs text-nx-muted lg:col-span-2">Actualizando estados…</p>
          ) : null}
        </div>
      )}
    </>
  )
}
