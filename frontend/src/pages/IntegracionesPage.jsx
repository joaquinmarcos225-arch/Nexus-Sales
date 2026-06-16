import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import { ROLE_LABELS, normalizeRole } from '../data/navigation.js'
import {
  disconnectUserProvider,
  fetchGoogleIntegrationVerify,
  fetchUserConnections,
  fetchGoogleOAuthStartUrl,
} from '../utils/api.js'

const FUTURE_PROVIDERS = [
  {
    id: 'linkedin',
    title: 'LinkedIn',
    description: 'Extensión de navegador para asistir envíos. Próximamente.',
  },
  {
    id: 'whatsapp',
    title: 'WhatsApp',
    description: 'Seguimiento por el canal acordado con el prospecto. Próximamente.',
  },
]

const EFFECTIVE_STATUS = {
  not_connected: {
    label: 'No conectado',
    pill: 'bg-zinc-100 text-zinc-700 ring-zinc-200/80',
  },
  functional: {
    label: 'Conectado y funcional',
    pill: 'bg-emerald-50 text-emerald-900 ring-emerald-200/80',
  },
  reconnect_required: {
    label: 'Reconexión requerida',
    pill: 'bg-amber-50 text-amber-950 ring-amber-200/80',
  },
  scope_missing: {
    label: 'Permisos insuficientes',
    pill: 'bg-rose-50 text-rose-900 ring-rose-200/80',
  },
  error: {
    label: 'Error de conexión',
    pill: 'bg-rose-50 text-rose-900 ring-rose-200/80',
  },
  pending: {
    label: 'Verificando…',
    pill: 'bg-sky-50 text-sky-900 ring-sky-200/80',
  },
}

function fmtDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-AR', {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return '—'
  }
}

function resolveEffectiveStatus(cardRow, verifyRow) {
  if (verifyRow?.effective_status) return verifyRow.effective_status
  const stored = String(cardRow?.status || 'not_connected').toLowerCase()
  if (stored === 'connected') {
    if (verifyRow?.requires_reconnect || verifyRow?.http_status === 401) {
      return 'reconnect_required'
    }
    if (verifyRow?.api_reachable) return 'functional'
    return 'reconnect_required'
  }
  if (stored === 'error') return 'reconnect_required'
  return 'not_connected'
}

function statusMeta(effectiveStatus) {
  return EFFECTIVE_STATUS[effectiveStatus] || EFFECTIVE_STATUS.error
}

function IntegrationCard({ title, description, effectiveStatus, email, lastActivity, children, footer }) {
  const meta = statusMeta(effectiveStatus)
  return (
    <article className="flex flex-col rounded-xl border border-nx-border bg-nx-card p-5 shadow-sm ring-1 ring-zinc-900/5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-nx-ink">{title}</h2>
          {description ? (
            <p className="mt-1 text-xs leading-relaxed text-nx-muted">{description}</p>
          ) : null}
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${meta.pill}`}
        >
          {meta.label}
        </span>
      </div>
      <dl className="mt-4 space-y-2 border-t border-nx-border/60 pt-3 text-xs text-nx-muted">
        <div className="flex justify-between gap-2">
          <dt>Cuenta conectada</dt>
          <dd className="text-right font-medium text-nx-ink">{email || '—'}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Última actividad</dt>
          <dd className="text-right text-nx-ink">{fmtDate(lastActivity)}</dd>
        </div>
      </dl>
      {children}
      {footer ? (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-nx-border/60 pt-4">{footer}</div>
      ) : null}
    </article>
  )
}

export default function IntegracionesPage() {
  const { user } = useAuth()
  const { companyId } = useCompany()
  const [searchParams, setSearchParams] = useSearchParams()
  const userId = user?.user_id ?? null
  const [cards, setCards] = useState([])
  const [verify, setVerify] = useState(null)
  const [loadingCards, setLoadingCards] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [oauthBanner, setOauthBanner] = useState(null)

  useEffect(() => {
    const ok = searchParams.get('google')
    const err = searchParams.get('google_error')
    const msg = searchParams.get('msg') || ''
    if (ok === 'connected') {
      setOauthBanner({
        type: 'ok',
        text: 'Google conectado correctamente. Gmail y Calendar quedaron vinculados a tu cuenta.',
      })
      const next = new URLSearchParams(searchParams)
      next.delete('google')
      setSearchParams(next, { replace: true })
    } else if (err) {
      setOauthBanner({
        type: 'err',
        text: `OAuth Google: ${err}${msg ? ` — ${decodeURIComponent(msg)}` : ''}`,
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
        const data = await fetchGoogleIntegrationVerify(companyId, userId, { deep })
        setVerify(data)
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
    void runVerify({ deep: false })
  }, [runVerify])

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
      : resolveEffectiveStatus(calRow, calVerify)
  const gmailEffective =
    verifying && !gmailVerify && String(gmailRow?.status || '').toLowerCase() === 'connected'
      ? 'pending'
      : resolveEffectiveStatus(gmailRow, gmailVerify)

  const googleLinked =
    calEffective !== 'not_connected' ||
    gmailEffective !== 'not_connected' ||
    String(gmailRow?.status || '').toLowerCase() === 'connected' ||
    String(calRow?.status || '').toLowerCase() === 'connected'

  const needsReconnect =
    calEffective === 'reconnect_required' ||
    gmailEffective === 'reconnect_required' ||
    calVerify?.requires_reconnect ||
    gmailVerify?.requires_reconnect

  async function runOp(key, fn) {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await refreshCards()
      await runVerify({ deep: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function startGoogleOAuth() {
    if (!companyId || userId == null) return
    setBusy('google-oauth')
    setError(null)
    try {
      const data = await fetchGoogleOAuthStartUrl(companyId, userId)
      const url = data?.authorization_url
      if (!url) {
        throw new Error('No se recibió la URL de autorización de Google')
      }
      window.location.href = url
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(null)
    }
  }

  const roleLabel = ROLE_LABELS[normalizeRole(user?.role)] || user?.role || '—'

  return (
    <>
      {oauthBanner?.type === 'ok' ? (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
          {oauthBanner.text}
        </div>
      ) : null}
      {oauthBanner?.type === 'err' ? (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-950">
          {oauthBanner.text}
        </div>
      ) : null}

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {verify?.oauth_configured === false ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          OAuth de Google no está configurado en el servidor (faltan GOOGLE_CLIENT_ID / SECRET).
        </div>
      ) : null}

      {!companyId || userId == null ? (
        <p className="rounded-lg border border-dashed border-nx-border bg-nx-card p-4 text-sm text-nx-muted">
          Iniciá sesión y seleccioná una empresa para gestionar tus integraciones.
        </p>
      ) : (
        <>
          <div className="mb-6 max-w-xl rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
              Tus integraciones
            </p>
            <p className="mt-2 text-sm font-medium text-nx-ink">
              {user?.name || user?.email} · {roleLabel}
            </p>
            <p className="mt-1 text-xs text-nx-muted">
              Gmail y Google Calendar se conectan con tu cuenta Google. Solo vos podés ver y gestionar
              estas conexiones.
            </p>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <IntegrationCard
              title="Google Calendar"
              description="Nexus crea eventos y sincroniza reuniones con tu calendario."
              effectiveStatus={calEffective}
              email={calVerify?.external_email || calRow?.external_email}
              lastActivity={calVerify?.updated_at || calRow?.updated_at}
              footer={
                <>
                  {!googleLinked || needsReconnect ? (
                    <button
                      type="button"
                      disabled={!!busy || loadingCards}
                      className="rounded-lg bg-nx-brand px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-40"
                      onClick={() => void startGoogleOAuth()}
                    >
                      {busy === 'google-oauth'
                        ? '…'
                        : needsReconnect
                          ? 'Reconectar Google Calendar'
                          : 'Conectar Google Calendar'}
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        disabled={!!busy || loadingCards}
                        className="rounded-lg border border-zinc-300 bg-white px-4 py-2 text-xs font-semibold text-zinc-800 shadow-sm hover:bg-zinc-50 disabled:opacity-40"
                        onClick={() => void startGoogleOAuth()}
                      >
                        Reconectar
                      </button>
                      <button
                        type="button"
                        disabled={!!busy || loadingCards}
                        className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-xs font-semibold text-rose-900 hover:bg-rose-100 disabled:opacity-40"
                        onClick={() =>
                          void runOp('google-disconnect', async () => {
                            await disconnectUserProvider(companyId, userId, 'gmail')
                            await disconnectUserProvider(companyId, userId, 'google_calendar')
                          })
                        }
                      >
                        {busy === 'google-disconnect' ? '…' : 'Desconectar'}
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    disabled={verifying || loadingCards}
                    className="rounded-lg border border-nx-border bg-white px-4 py-2 text-xs font-semibold text-nx-ink shadow-sm hover:bg-nx-card-muted disabled:opacity-40"
                    onClick={() => void runVerify({ deep: true })}
                  >
                    {verifying ? 'Verificando…' : 'Verificar permisos'}
                  </button>
                </>
              }
            >
              <div className="mt-3 rounded-lg border border-nx-border/80 bg-nx-card-muted/30 p-3 text-xs">
                <p className="font-semibold text-nx-ink">Permisos Nexus</p>
                {verifying ? (
                  <p className="mt-1 text-nx-muted">Comprobando Calendar API, disponibilidad y creación…</p>
                ) : calEffective === 'not_connected' ? (
                  <p className="mt-1 text-nx-muted">Conectá Google para validar permisos de Calendar.</p>
                ) : (
                  <ul className="mt-2 space-y-1 text-nx-muted">
                    <li>
                      API Calendar:{' '}
                      <span
                        className={
                          calVerify?.api_reachable ? 'text-emerald-800' : 'text-amber-900'
                        }
                      >
                        {calVerify?.api_reachable ? 'accesible' : 'sin acceso'}
                      </span>
                      {calVerify?.http_status ? ` (HTTP ${calVerify.http_status})` : ''}
                    </li>
                    <li>
                      Leer disponibilidad:{' '}
                      <span
                        className={
                          calVerify?.can_read_availability ? 'text-emerald-800' : 'text-amber-900'
                        }
                      >
                        {calVerify?.can_read_availability ? 'sí' : 'no'}
                      </span>
                    </li>
                    <li>
                      Crear eventos:{' '}
                      <span
                        className={
                          calVerify?.create_event_verified ? 'text-emerald-800' : 'text-amber-900'
                        }
                      >
                        {calVerify?.create_event_verified
                          ? 'verificado'
                          : calVerify?.can_create_events
                            ? 'sí'
                            : 'no verificado'}
                      </span>
                    </li>
                    {calVerify?.verification_summary ? (
                      <li className="text-nx-ink">{calVerify.verification_summary}</li>
                    ) : null}
                    {calVerify?.api_error ? (
                      <li className="text-rose-800">{calVerify.api_error}</li>
                    ) : null}
                  </ul>
                )}
              </div>
            </IntegrationCard>

            <IntegrationCard
              title="Gmail"
              description="Envío y recepción de correos comerciales desde tu cuenta."
              effectiveStatus={gmailEffective}
              email={gmailVerify?.external_email || gmailRow?.external_email}
              lastActivity={gmailVerify?.updated_at || gmailRow?.updated_at}
              footer={
                !googleLinked || needsReconnect ? (
                  <button
                    type="button"
                    disabled={!!busy || loadingCards}
                    className="rounded-lg bg-nx-brand px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-40"
                    onClick={() => void startGoogleOAuth()}
                  >
                    {needsReconnect ? 'Reconectar Gmail' : 'Conectar Gmail'}
                  </button>
                ) : (
                  <p className="text-xs text-nx-muted">
                    Gmail comparte la sesión Google con Calendar. Usá Reconectar o Desconectar en
                    Calendar.
                  </p>
                )
              }
            >
              <div className="mt-3 rounded-lg border border-nx-border/80 bg-nx-card-muted/30 p-3 text-xs">
                <p className="font-semibold text-nx-ink">Estado API</p>
                {verifying ? (
                  <p className="mt-1 text-nx-muted">Comprobando acceso a Gmail…</p>
                ) : gmailEffective === 'not_connected' ? (
                  <p className="mt-1 text-nx-muted">Conectá Google para validar acceso a Gmail.</p>
                ) : (
                  <ul className="mt-2 space-y-1 text-nx-muted">
                    <li>
                      API Gmail:{' '}
                      <span
                        className={
                          gmailVerify?.api_reachable ? 'text-emerald-800' : 'text-amber-900'
                        }
                      >
                        {gmailVerify?.api_reachable ? 'accesible' : 'sin acceso'}
                      </span>
                      {gmailVerify?.http_status ? ` (HTTP ${gmailVerify.http_status})` : ''}
                    </li>
                    {gmailVerify?.verification_summary ? (
                      <li className="text-nx-ink">{gmailVerify.verification_summary}</li>
                    ) : null}
                    {gmailVerify?.api_error ? (
                      <li className="text-rose-800">{gmailVerify.api_error}</li>
                    ) : null}
                  </ul>
                )}
              </div>
            </IntegrationCard>

            {FUTURE_PROVIDERS.map((def) => (
              <article
                key={def.id}
                className="flex flex-col rounded-xl border border-dashed border-nx-border bg-nx-card-muted/20 p-5 opacity-80"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h2 className="text-base font-semibold text-nx-ink">{def.title}</h2>
                    <p className="mt-1 text-xs leading-relaxed text-nx-muted">{def.description}</p>
                  </div>
                  <span className="shrink-0 rounded-full bg-zinc-100 px-2.5 py-1 text-[11px] font-semibold text-zinc-600 ring-1 ring-zinc-200/80">
                    Próximamente
                  </span>
                </div>
              </article>
            ))}
          </div>

          {loadingCards ? (
            <p className="mt-4 text-xs text-nx-muted">Actualizando estados…</p>
          ) : null}
        </>
      )}
    </>
  )
}
