import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { PageHeader } from '../layout/PageHeader'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import {
  disconnectUserProvider,
  fetchUserConnections,
  fetchUsers,
  fetchGoogleOAuthStartUrl,
  mockConnectUserProvider,
  mockErrorUserProvider,
} from '../utils/api.js'

const STORAGE_PREFIX = 'nexus_connections_user_id_'

const OTHER_PROVIDERS = [
  {
    id: 'whatsapp',
    title: 'WhatsApp',
    description: 'Mensajes de seguimiento por el canal acordado con el prospecto (mock hasta integrar canal).',
  },
  {
    id: 'linkedin',
    title: 'LinkedIn',
    description: 'Extensión de navegador para asistir envíos; sin pedir API keys personales.',
  },
]

function roleLabel(role) {
  const r = String(role || '').toLowerCase()
  if (r === 'seller') {
    return 'SDR / Vendedor'
  }
  if (r === 'manager') {
    return 'Manager'
  }
  if (r === 'admin') {
    return 'Admin'
  }
  return r || '—'
}

function statusLabel(provider, status) {
  const s = String(status || '').toLowerCase()
  if (provider === 'linkedin') {
    if (s === 'extension_not_installed') {
      return 'Extensión no instalada'
    }
    if (s === 'extension_connected') {
      return 'Extensión conectada'
    }
  }
  if (s === 'connected') {
    return 'Conectado'
  }
  if (s === 'error') {
    return 'Error'
  }
  return 'No conectado'
}

function statusPillClass(provider, status) {
  const s = String(status || '').toLowerCase()
  if (s === 'error') {
    return 'bg-rose-50 text-rose-900 ring-rose-200/80'
  }
  if (s === 'connected' || s === 'extension_connected') {
    return 'bg-emerald-50 text-emerald-900 ring-emerald-200/80'
  }
  if (provider === 'linkedin' && s === 'extension_not_installed') {
    return 'bg-amber-50 text-amber-950 ring-amber-200/80'
  }
  return 'bg-zinc-100 text-zinc-700 ring-zinc-200/80'
}

function fmtDate(iso) {
  if (!iso) {
    return '—'
  }
  try {
    return new Date(iso).toLocaleString('es-AR', {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return '—'
  }
}

export default function ConexionesPage() {
  const { companyId } = useCompany()
  const [searchParams, setSearchParams] = useSearchParams()
  const [users, setUsers] = useState([])
  const [userId, setUserId] = useState(null)
  const [cards, setCards] = useState([])
  const [loadingUsers, setLoadingUsers] = useState(true)
  const [loadingCards, setLoadingCards] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [oauthBanner, setOauthBanner] = useState(null)

  useEffect(() => {
    const ok = searchParams.get('google')
    const err = searchParams.get('google_error')
    const msg = searchParams.get('msg') || ''
    if (ok === 'connected') {
      setOauthBanner({ type: 'ok', text: 'Google conectado: Gmail y Calendar quedaron vinculados a tu cuenta.' })
      const next = new URLSearchParams(searchParams)
      next.delete('google')
      setSearchParams(next, { replace: true })
    } else if (err) {
      setOauthBanner({ type: 'err', text: `OAuth Google: ${err}${msg ? ` — ${decodeURIComponent(msg)}` : ''}` })
      const next = new URLSearchParams(searchParams)
      next.delete('google_error')
      next.delete('msg')
      setSearchParams(next, { replace: true })
    }
  }, [searchParams, setSearchParams])

  useEffect(() => {
    if (!companyId) {
      setUsers([])
      setUserId(null)
      setLoadingUsers(false)
      return
    }
    let cancelled = false
    ;(async () => {
      setLoadingUsers(true)
      setError(null)
      try {
        const list = await fetchUsers(companyId)
        if (cancelled) {
          return
        }
        const rows = Array.isArray(list) ? list : []
        setUsers(rows)
        const key = `${STORAGE_PREFIX}${companyId}`
        const rawSaved = localStorage.getItem(key)
        const savedNum =
          rawSaved != null && rawSaved !== '' ? Number.parseInt(rawSaved, 10) : Number.NaN
        const fromStorage =
          Number.isFinite(savedNum) && rows.some((u) => Number(u.id) === savedNum) ? savedNum : null
        const seller = rows.find((u) => String(u.role).toLowerCase() === 'seller')
        const pick = fromStorage ?? seller?.id ?? rows[0]?.id ?? null
        setUserId(pick != null ? Number(pick) : null)
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setUsers([])
          setUserId(null)
        }
      } finally {
        if (!cancelled) {
          setLoadingUsers(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [companyId])

  useEffect(() => {
    if (userId != null && companyId != null) {
      localStorage.setItem(`${STORAGE_PREFIX}${companyId}`, String(userId))
    }
  }, [companyId, userId])

  const refreshCards = useCallback(async () => {
    if (!companyId || userId == null) {
      setCards([])
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

  useEffect(() => {
    void refreshCards()
  }, [refreshCards])

  const byProvider = useMemo(() => {
    const m = new Map()
    for (const c of cards) {
      m.set(String(c.provider).toLowerCase(), c)
    }
    return m
  }, [cards])

  const gmailRow = byProvider.get('gmail')
  const calRow = byProvider.get('google_calendar')
  const googleFullyConnected =
    String(gmailRow?.status || '').toLowerCase() === 'connected' &&
    String(calRow?.status || '').toLowerCase() === 'connected'

  async function runOp(key, fn) {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await refreshCards()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function primaryAction(provider, row) {
    const st = String(row?.status || 'not_connected').toLowerCase()
    if (provider === 'linkedin') {
      if (st === 'extension_connected') {
        return { label: 'Desconectar', action: 'disconnect' }
      }
      return {
        label: st === 'extension_not_installed' ? 'Completar conexión' : 'Conectar LinkedIn / Instalar extensión',
        action: 'connect',
      }
    }
    if (st === 'connected') {
      return { label: 'Desconectar', action: 'disconnect' }
    }
    return {
      label: `Conectar ${OTHER_PROVIDERS.find((p) => p.id === provider)?.title ?? ''}`.trim(),
      action: 'connect',
    }
  }

  async function startGoogleOAuth() {
    if (!companyId || userId == null) {
      return
    }
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

  return (
    <>
      <PageHeader
        title="Conexiones"
        description="Gmail y Google Calendar se enlazan con un solo inicio de sesión de Google (OAuth). WhatsApp y LinkedIn siguen en modo simulación. No pedimos contraseñas ni API keys al SDR."
      />

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

      {!companyId ? (
        <p className="rounded-lg border border-dashed border-nx-border bg-nx-card p-4 text-sm text-nx-muted">
          Seleccioná una empresa en el header para gestionar conexiones.
        </p>
      ) : loadingUsers ? (
        <p className="text-sm text-nx-muted">Cargando usuarios…</p>
      ) : users.length === 0 ? (
        <p className="text-sm text-nx-muted">No hay usuarios en esta empresa.</p>
      ) : (
        <div className="mb-6 max-w-xl rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
          <label htmlFor="conn-user" className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
            Conectar cuentas como
          </label>
          <select
            id="conn-user"
            className="mt-2 w-full rounded-lg border border-nx-border bg-white px-3 py-2 text-sm text-nx-ink"
            value={userId ?? ''}
            onChange={(e) => setUserId(Number(e.target.value))}
          >
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} ({u.email}) · {roleLabel(u.role)}
              </option>
            ))}
          </select>
        </div>
      )}

      {companyId && userId != null ? (
        <div className="grid gap-4 md:grid-cols-2">
          <article className="flex flex-col rounded-xl border border-nx-border bg-nx-card p-5 shadow-sm ring-1 ring-zinc-900/5 md:col-span-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h2 className="text-base font-semibold text-nx-ink">Google (Gmail + Calendar)</h2>
                <p className="mt-1 text-xs leading-relaxed text-nx-muted">
                  Un solo consentimiento de Google. Nexus guarda tokens cifrados en el servidor; todavía no enviamos
                  mails ni creamos eventos automáticamente.
                </p>
              </div>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-nx-border/80 bg-nx-card-muted/40 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">Gmail</p>
                <p
                  className={`mt-2 inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${statusPillClass('gmail', gmailRow?.status)}`}
                >
                  {statusLabel('gmail', gmailRow?.status)}
                </p>
                <p className="mt-2 text-xs text-nx-muted">
                  Cuenta: <span className="font-medium text-nx-ink">{gmailRow?.external_email || '—'}</span>
                </p>
                <p className="mt-1 text-xs text-nx-muted">
                  Conectado: {fmtDate(gmailRow?.connected_at)}
                </p>
              </div>
              <div className="rounded-lg border border-nx-border/80 bg-nx-card-muted/40 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">Google Calendar</p>
                <p
                  className={`mt-2 inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${statusPillClass('google_calendar', calRow?.status)}`}
                >
                  {statusLabel('google_calendar', calRow?.status)}
                </p>
                <p className="mt-2 text-xs text-nx-muted">
                  Cuenta: <span className="font-medium text-nx-ink">{calRow?.external_email || '—'}</span>
                </p>
                <p className="mt-1 text-xs text-nx-muted">
                  Conectado: {fmtDate(calRow?.connected_at)}
                </p>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              {googleFullyConnected ? (
                <button
                  type="button"
                  disabled={!!busy || loadingCards}
                  className="rounded-lg border border-zinc-300 bg-white px-4 py-2 text-xs font-semibold text-zinc-800 shadow-sm hover:bg-zinc-50 disabled:opacity-40"
                  onClick={() =>
                    void runOp('google-disconnect', async () => {
                      await disconnectUserProvider(companyId, userId, 'gmail')
                      await disconnectUserProvider(companyId, userId, 'google_calendar')
                    })
                  }
                >
                  {busy === 'google-disconnect' ? '…' : 'Desconectar Google'}
                </button>
              ) : (
                <button
                  type="button"
                  disabled={!!busy || loadingCards}
                  className="rounded-lg bg-nx-brand px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-40"
                  onClick={() => void startGoogleOAuth()}
                >
                  Conectar Google
                </button>
              )}
            </div>
          </article>

          {OTHER_PROVIDERS.map((def) => {
            const row = byProvider.get(def.id) ?? {
              provider: def.id,
              status: 'not_connected',
              external_email: null,
              connected_at: null,
              updated_at: null,
            }
            const st = String(row.status || 'not_connected').toLowerCase()
            const primary = primaryAction(def.id, row)
            const opKey = (op) => `${def.id}-${op}`

            return (
              <article
                key={def.id}
                className="flex flex-col rounded-xl border border-nx-border bg-nx-card p-5 shadow-sm ring-1 ring-zinc-900/5"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h2 className="text-base font-semibold text-nx-ink">{def.title}</h2>
                    <p className="mt-1 text-xs leading-relaxed text-nx-muted">{def.description}</p>
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${statusPillClass(def.id, st)}`}
                  >
                    {statusLabel(def.id, st)}
                  </span>
                </div>
                <dl className="mt-4 space-y-1 border-t border-nx-border/60 pt-3 text-xs text-nx-muted">
                  <div className="flex justify-between gap-2">
                    <dt>Cuenta</dt>
                    <dd className="text-right font-medium text-nx-ink">{row.external_email || '—'}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt>Conectado desde</dt>
                    <dd className="text-right text-nx-ink">{fmtDate(row.connected_at)}</dd>
                  </div>
                </dl>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={!!busy || loadingCards}
                    className="rounded-lg bg-nx-brand px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-nx-brand-hover disabled:opacity-40"
                    onClick={() =>
                      void runOp(opKey('main'), async () => {
                        if (primary.action === 'disconnect') {
                          await disconnectUserProvider(companyId, userId, def.id)
                        } else {
                          await mockConnectUserProvider(companyId, userId, def.id)
                        }
                      })
                    }
                  >
                    {busy === opKey('main') ? '…' : primary.label}
                  </button>
                  <button
                    type="button"
                    disabled={!!busy || loadingCards}
                    className="rounded-lg border border-transparent px-2 py-1.5 text-[11px] font-medium text-nx-muted underline-offset-2 hover:text-nx-ink hover:underline disabled:opacity-40"
                    onClick={() =>
                      void runOp(opKey('err'), async () => {
                        await mockErrorUserProvider(companyId, userId, def.id)
                      })
                    }
                  >
                    {busy === opKey('err') ? '…' : 'Simular error (mock)'}
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      ) : null}

      {loadingCards && userId != null ? (
        <p className="mt-4 text-xs text-nx-muted">Actualizando estados…</p>
      ) : null}
    </>
  )
}
