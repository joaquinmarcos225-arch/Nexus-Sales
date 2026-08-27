import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchMe,
  fetchThread,
  fetchThreads,
  getToken,
  patchThreadStatus,
  replyThread,
  setToken,
  supportLogin,
} from './api.js'
import { enableSupportPush, notifySupportInbox, requestSupportNotificationPermission } from './notifications.js'
import ObservabilityPanel from './ObservabilityPanel.jsx'

const FILTERS = [
  { id: 'all', label: 'Bandeja', chip: 'bg-white/15 text-white' },
  { id: 'open', label: 'Abiertos', chip: 'bg-sky-500/20 text-sky-200' },
  { id: 'waiting', label: 'Esperando', chip: 'bg-amber-500/25 text-amber-200' },
  { id: 'resolved', label: 'Resueltos', chip: 'bg-emerald-500/20 text-emerald-200' },
]

function fmtWhen(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('es-AR', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}

function LogoMark({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden>
      <circle cx="32" cy="32" r="27" stroke="rgba(248,113,113,0.4)" strokeWidth="1" strokeDasharray="3 5" />
      <circle cx="32" cy="32" r="8" fill="#dc2626" />
      <circle cx="32" cy="32" r="3.5" fill="#fff" />
    </svg>
  )
}

function LoginScreen({ onLoggedIn }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(ev) {
    ev.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await supportLogin(email.trim(), password)
      if (!res?.user?.is_support_ops) {
        setToken(null)
        throw new Error('Esta cuenta no pertenece al equipo de Nexus Support.')
      }
      setToken(res.access_token)
      onLoggedIn(res.user)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-[#0c0606] px-4">
      <form
        onSubmit={(e) => void handleSubmit(e)}
        className="w-full max-w-sm rounded-2xl border border-white/10 bg-white p-6 shadow-xl"
      >
        <div className="flex items-center gap-2">
          <LogoMark />
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-red-600">Nexus</p>
            <h1 className="text-lg font-semibold text-slate-900">Support</h1>
          </div>
        </div>
        <p className="mt-2 text-sm text-slate-500">App interna del equipo. No es Nexus Sales.</p>
        {error ? <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p> : null}
        <label className="mt-4 block text-xs font-medium text-slate-600">
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-red-400 focus:ring-2 focus:ring-red-100"
            autoComplete="username"
            required
          />
        </label>
        <label className="mt-3 block text-xs font-medium text-slate-600">
          Contraseña
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-red-400 focus:ring-2 focus:ring-red-100"
            autoComplete="current-password"
            required
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="mt-5 w-full rounded-xl bg-red-600 py-2.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
        >
          {busy ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </div>
  )
}

function matchesFilter(item, filter) {
  const status = item.status || 'open'
  if (filter === 'open') return status !== 'resolved'
  if (filter === 'waiting') return Boolean(item.waiting)
  if (filter === 'resolved') return status === 'resolved'
  return true
}

function Inbox({ user, onLogout }) {
  const [items, setItems] = useState([])
  const [section, setSection] = useState('inbox')
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [thread, setThread] = useState(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loadingList, setLoadingList] = useState(true)
  const [navOpen, setNavOpen] = useState(false)
  const [notifPerm, setNotifPerm] = useState(
    typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,
  )
  const bottomRef = useRef(null)
  const prevWaiting = useRef(null)

  const counts = useMemo(() => {
    const open = items.filter((i) => (i.status || 'open') !== 'resolved').length
    const waiting = items.filter((i) => i.waiting).length
    const resolved = items.filter((i) => i.status === 'resolved').length
    return { all: items.length, open, waiting, resolved }
  }, [items])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return items.filter((it) => {
      if (!matchesFilter(it, filter)) return false
      if (!q) return true
      const hay = `${it.company_name || ''} ${it.preview || ''} ${it.opened_by_name || ''}`.toLowerCase()
      return hay.includes(q)
    })
  }, [items, filter, query])

  const loadList = useCallback(async () => {
    try {
      const data = await fetchThreads()
      setItems(Array.isArray(data?.items) ? data.items : [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingList(false)
    }
  }, [])

  const loadThread = useCallback(async (id) => {
    if (!id) return
    try {
      const data = await fetchThread(id)
      setThread(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    if (section !== 'inbox') return undefined
    void loadList()
    const t = window.setInterval(() => void loadList(), 12000)
    return () => window.clearInterval(t)
  }, [loadList, section])

  useEffect(() => {
    if (section !== 'inbox') return
    void enableSupportPush().then((perm) => {
      if (perm) setNotifPerm(perm)
    })
  }, [])

  useEffect(() => {
    if (prevWaiting.current == null) {
      prevWaiting.current = counts.waiting
      return
    }
    if (counts.waiting > prevWaiting.current) {
      const delta = counts.waiting - prevWaiting.current
      void notifySupportInbox({
        title: delta === 1 ? 'Nuevo mensaje de un cliente' : `${delta} mensajes nuevos`,
        body: 'Abrí Nexus Support para responder.',
      })
    }
    prevWaiting.current = counts.waiting
  }, [counts.waiting])

  useEffect(() => {
    if (section !== 'inbox') {
      if (!selectedId) setThread(null)
      return undefined
    }
    if (!selectedId) {
      setThread(null)
      return undefined
    }
    void loadThread(selectedId)
    const t = window.setInterval(() => void loadThread(selectedId), 8000)
    return () => window.clearInterval(t)
  }, [selectedId, loadThread, section])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [thread?.messages?.length])

  async function handleSend(ev) {
    ev.preventDefault()
    const text = draft.trim()
    if (!text || !selectedId || busy) return
    setBusy(true)
    try {
      const data = await replyThread(selectedId, text)
      setThread(data)
      setDraft('')
      await loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleStatus(next) {
    if (!selectedId || busy) return
    setBusy(true)
    try {
      const data = await patchThreadStatus(selectedId, next)
      setThread(data)
      await loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const selectedStatus = thread?.status || items.find((i) => i.id === selectedId)?.status || 'open'
  const composerEnabled = Boolean(selectedId) && selectedStatus !== 'resolved'

  return (
    <div className="flex h-dvh min-h-0 bg-[#140808] text-[#1a1010]">
      {navOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          aria-label="Cerrar menú"
          onClick={() => setNavOpen(false)}
        />
      ) : null}
      <aside
        className={[
          'ns-chrome fixed inset-y-0 left-0 z-50 flex w-52 shrink-0 flex-col text-zinc-100 transition-transform lg:static lg:z-auto lg:translate-x-0',
          navOpen ? 'translate-x-0' : '-translate-x-full',
        ].join(' ')}
      >
        <div className="flex items-center gap-2 border-b border-red-500/20 px-4 py-4">
          <LogoMark />
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-red-400">Nexus</p>
            <p className="text-sm font-semibold text-white">Support</p>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-2 py-3">
          <button
            type="button"
            onClick={() => {
              setSection('operations')
              setNavOpen(false)
            }}
            className={[
              'mb-3 flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm',
              section === 'operations'
                ? 'bg-red-600 text-white shadow-sm shadow-red-900/40'
                : 'text-zinc-300 hover:bg-white/10 hover:text-white',
            ].join(' ')}
          >
            <span>Operaciones</span>
            <span className={`size-2 rounded-full ${section === 'operations' ? 'bg-white' : 'bg-emerald-400'}`} />
          </button>
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Soporte</p>
          {FILTERS.map((f) => {
            const n =
              f.id === 'all'
                ? counts.all
                : f.id === 'open'
                  ? counts.open
                  : f.id === 'waiting'
                    ? counts.waiting
                    : counts.resolved
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => {
                  setSection('inbox')
                  setFilter(f.id)
                  setNavOpen(false)
                }}
                className={[
                  'flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm',
                  filter === f.id
                    ? 'bg-red-600 text-white shadow-sm shadow-red-900/40'
                    : 'text-zinc-300 hover:bg-white/10 hover:text-white',
                ].join(' ')}
              >
                <span>{f.label}</span>
                <span className={`rounded-full px-1.5 text-[11px] tabular-nums ${filter === f.id ? 'bg-white/20' : f.chip}`}>
                  {n}
                </span>
              </button>
            )
          })}
        </nav>
        <div className="border-t border-red-500/20 px-4 py-3 text-xs text-zinc-400">
          <p className="font-semibold uppercase tracking-wide text-red-300/80">Equipo</p>
          <p className="mt-1 truncate text-zinc-100">{user?.name || user?.email}</p>
          <p className="truncate text-zinc-400">{user?.email}</p>
          <button
            type="button"
            onClick={onLogout}
            className="mt-3 w-full rounded-lg border border-red-400/30 py-1.5 text-xs font-semibold text-white hover:bg-red-600/30"
          >
            Salir
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="ns-chrome flex flex-wrap items-center justify-between gap-3 border-b border-red-500/20 px-3 py-3 text-white sm:px-5">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              className="rounded-lg border border-white/20 p-2 text-white lg:hidden"
              aria-label="Abrir menú"
              onClick={() => setNavOpen(true)}
            >
              <svg className="size-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
              </svg>
            </button>
            <div className="min-w-0">
              <h1 className="text-base font-semibold text-white">
                {section === 'operations' ? 'Operaciones' : 'Bandeja'}
              </h1>
              <p className="hidden text-xs text-red-100/70 sm:block">
                {section === 'operations'
                  ? 'Salud, costos, límites y automatizaciones de Nexus'
                  : 'Tickets de clientes de Nexus Sales → Soporte'}
              </p>
            </div>
          </div>
          {section === 'inbox' ? <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-sky-500/25 px-2.5 py-1 font-medium text-sky-100">{counts.open} abiertos</span>
            <span className="rounded-full bg-amber-400/25 px-2.5 py-1 font-medium text-amber-100">
              {counts.waiting} esperando
            </span>
            <span className="rounded-full bg-emerald-400/25 px-2.5 py-1 font-medium text-emerald-100">
              {counts.resolved} resueltos
            </span>
          </div> : null}
        </header>

        {error ? <p className="border-b border-red-300 bg-red-700 px-5 py-2 text-sm text-white">{error}</p> : null}
        {section === 'inbox' && notifPerm === 'default' ? (
          <button
            type="button"
            className="border-b border-amber-400/40 bg-amber-500/20 px-4 py-2 text-left text-xs text-amber-100"
            onClick={() => {
              void requestSupportNotificationPermission().then((p) => {
                setNotifPerm(p)
                if (p === 'granted') void enableSupportPush()
              })
            }}
          >
            Activar notificaciones para tickets nuevos.
          </button>
        ) : null}

        {section === 'operations' ? (
          <ObservabilityPanel />
        ) : (
        <div className="grid min-h-0 flex-1 lg:grid-cols-[20rem_minmax(0,1fr)]">
          <section
            className={[
              'ns-list min-h-0 flex-col border-r border-rose-200',
              selectedId ? 'hidden lg:flex' : 'flex',
            ].join(' ')}
          >
            <div className="border-b border-rose-200/80 px-3 py-2">
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Buscar empresa o mensaje…"
                className="w-full rounded-lg border border-rose-200 bg-white px-3 py-2 text-sm outline-none focus:border-red-500 focus:ring-2 focus:ring-red-200"
              />
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {loadingList ? (
                <p className="px-4 py-8 text-center text-sm text-rose-400">Cargando bandeja…</p>
              ) : visible.length === 0 ? (
                <div className="mx-3 my-4 rounded-xl border border-dashed border-rose-300 bg-white/70 px-4 py-8 text-center">
                  <p className="text-sm font-semibold text-rose-950">Aún no hay tickets</p>
                  <p className="mt-1 text-xs leading-relaxed text-rose-800/80">
                    Cuando un cliente escriba en Nexus Sales → <strong>Soporte</strong>, aparece acá.
                  </p>
                </div>
              ) : (
                visible.map((it) => {
                  const active = selectedId === it.id
                  return (
                    <button
                      key={it.id}
                      type="button"
                      onClick={() => setSelectedId(it.id)}
                      className={[
                        'w-full border-b border-rose-200/70 px-4 py-3 text-left',
                        active
                          ? 'border-l-4 border-l-red-600 bg-white shadow-sm'
                          : 'border-l-4 border-l-transparent hover:bg-white/70',
                      ].join(' ')}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="truncate text-sm font-semibold text-rose-950">
                          {it.opened_by_name || it.opened_by_email || 'Usuario'}
                        </p>
                        {it.waiting ? (
                          <span className="shrink-0 rounded-full bg-amber-400 px-1.5 py-0.5 text-[10px] font-semibold text-amber-950">
                            Esperando
                          </span>
                        ) : it.status === 'resolved' ? (
                          <span className="shrink-0 rounded-full bg-emerald-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                            Resuelto
                          </span>
                        ) : (
                          <span className="shrink-0 rounded-full bg-sky-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                            Abierto
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-xs text-rose-800/80">{it.preview || 'Sin mensajes todavía'}</p>
                      <p className="mt-1 text-[10px] text-rose-400">
                        {it.company_name || `Empresa ${it.company_id}`}
                        {it.opened_by_email ? ` · ${it.opened_by_email}` : ''}
                        {' · '}
                        {fmtWhen(it.last_message_at) || '—'}
                      </p>
                    </button>
                  )
                })
              )}
            </div>
          </section>

          <section
            className={[
              'ns-chat min-h-0 flex-col',
              selectedId ? 'flex' : 'hidden lg:flex',
            ].join(' ')}
          >
            {selectedId && thread ? (
              <div className="flex items-center justify-between gap-3 border-b border-rose-200 bg-white px-4 py-2.5">
                <div className="flex min-w-0 items-center gap-2">
                  <button
                    type="button"
                    className="rounded-lg border border-rose-200 px-2 py-1 text-xs font-semibold text-rose-900 lg:hidden"
                    onClick={() => setSelectedId(null)}
                  >
                    ← Lista
                  </button>
                  <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-rose-950">
                    {thread.opened_by_name || thread.opened_by_email || 'Usuario'}
                  </h2>
                  <p className="text-xs text-rose-700/80">
                    {thread.company_name || `Empresa ${thread.company_id}`}
                    {thread.opened_by_email ? ` · ${thread.opened_by_email}` : ''}
                    {' · '}
                    Lo ve en Nexus Sales → Soporte
                  </p>
                  </div>
                </div>
                {selectedStatus === 'resolved' ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleStatus('open')}
                    className="shrink-0 rounded-lg border border-rose-200 px-3 py-1.5 text-xs font-semibold text-rose-900 hover:bg-rose-50 disabled:opacity-40"
                  >
                    Reabrir
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleStatus('resolved')}
                    className="shrink-0 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-40"
                  >
                    Marcar resuelto
                  </button>
                )}
              </div>
            ) : (
              <div className="border-b border-rose-200 bg-white px-4 py-2.5">
                <h2 className="text-sm font-semibold text-rose-950">Conversación</h2>
                <p className="text-xs text-rose-700/80">Elegí un ticket a la izquierda para responder</p>
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
              {!selectedId ? (
                <div className="mx-auto mt-8 max-w-md rounded-2xl border border-rose-200 bg-rose-50/60 px-5 py-8 text-center">
                  <LogoMark size={40} />
                  <p className="mt-3 text-sm font-semibold text-rose-950">Nada que atender todavía</p>
                  <p className="mt-1 text-xs text-rose-700/80">No hay clientes usando Soporte. La bandeja está lista.</p>
                  <ol className="mt-4 space-y-1.5 text-left text-xs text-rose-900/80">
                    <li>1. El cliente entra a Nexus Sales → Soporte</li>
                    <li>2. Escribe un mensaje</li>
                    <li>3. El ticket aparece en esta bandeja</li>
                    <li>4. Respondés acá; el cliente lo ve en Soporte</li>
                  </ol>
                </div>
              ) : (thread?.messages || []).length === 0 ? (
                <p className="py-12 text-center text-sm text-rose-400">Este ticket no tiene mensajes.</p>
              ) : (
                <div className="space-y-3">
                  {(thread?.messages || []).map((m) => {
                    const mine = m.role === 'support'
                    return (
                      <div key={m.id} className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                        <div
                          className={[
                            'max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm shadow-sm',
                            mine
                              ? 'rounded-br-md bg-red-600 text-white'
                              : 'rounded-bl-md border border-rose-200 bg-rose-50 text-rose-950',
                          ].join(' ')}
                        >
                          <p
                            className={`mb-1 text-[10px] font-semibold uppercase tracking-wide ${mine ? 'text-red-100' : 'text-rose-500'}`}
                          >
                            {mine ? 'Nexus Support' : 'Cliente'}
                          </p>
                          <p className="whitespace-pre-wrap leading-relaxed">{m.text}</p>
                          <p className={`mt-1 text-[10px] ${mine ? 'text-red-100/70' : 'text-rose-400'}`}>
                            {fmtWhen(m.at)}
                          </p>
                        </div>
                      </div>
                    )
                  })}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>

            <form
              onSubmit={(e) => void handleSend(e)}
              className="border-t border-rose-200 bg-white px-3 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
            >
              <div className="flex items-end gap-2">
                <textarea
                  rows={2}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={
                    !selectedId
                      ? 'Cuando haya un ticket, escribí la respuesta acá…'
                      : selectedStatus === 'resolved'
                        ? 'Ticket resuelto. Reabrilo para responder.'
                        : 'Responder al cliente…'
                  }
                  className="min-h-[2.75rem] flex-1 resize-none rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm text-rose-950 placeholder:text-rose-400 outline-none focus:border-red-500 focus:ring-2 focus:ring-red-100 disabled:bg-rose-50 disabled:opacity-60"
                  disabled={!composerEnabled || busy}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      void handleSend(e)
                    }
                  }}
                />
                <button
                  type="submit"
                  disabled={!composerEnabled || busy || !draft.trim()}
                  className="shrink-0 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-40"
                >
                  {busy ? '…' : 'Enviar'}
                </button>
              </div>
            </form>
          </section>
        </div>
        )}
      </div>
    </div>
  )
}

export default function App() {
  const [user, setUser] = useState(null)
  const [booting, setBooting] = useState(Boolean(getToken()))

  useEffect(() => {
    if (!getToken()) {
      setBooting(false)
      return
    }
    fetchMe()
      .then((me) => {
        if (!me?.is_support_ops) {
          setToken(null)
          setUser(null)
          return
        }
        setUser(me)
      })
      .catch(() => {
        setToken(null)
        setUser(null)
      })
      .finally(() => setBooting(false))
  }, [])

  if (booting) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-[#0c0606] text-sm text-zinc-400">Cargando Nexus Support…</div>
    )
  }

  if (!user) {
    return <LoginScreen onLoggedIn={setUser} />
  }

  return (
    <Inbox
      user={user}
      onLogout={() => {
        setToken(null)
        setUser(null)
      }}
    />
  )
}
