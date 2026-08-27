import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  assignSellerCredits,
  fetchCreditLedger,
  fetchCreditPeerTransfers,
  transferSellerCredits,
} from '../../utils/api.js'
import { formatContactCredits } from '../../utils/format.js'
import { normalizeRole, ROLE_LABELS, ROLES } from '../../data/navigation.js'
import { notifyCreditsChanged } from '../../hooks/useMyCredits.js'

function initials(name, email) {
  const src = (name || email || '?').trim()
  const parts = src.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return src.slice(0, 2).toUpperCase()
}

function roleLabel(role) {
  const r = normalizeRole(role)
  return ROLE_LABELS[r] || (r === ROLES.manager ? 'Manager' : r === ROLES.sdr ? 'SDR' : '')
}

function formatTransferWhen(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('es-AR', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * UI tipo chat para transferir / asignar créditos.
 *
 * mode="transfer" — peer-to-peer (SDR/Manager)
 * mode="assign" — director → managers desde el pool
 */
export function CreditsTransferChat({
  companyId,
  myUserId,
  peers,
  myAvailable,
  disabled = false,
  onTransferred,
  mode = 'transfer',
  title = 'Enviar créditos al equipo',
  description = 'Transferí desde tu saldo personal a cualquier SDR o Manager de la empresa.',
  balanceLabel = 'Tu saldo',
  emptyPeersTitle = 'Sin destinatarios',
  emptyPeersHint = 'Cuando haya otros SDR o Managers en la empresa, vas a poder transferirles créditos acá.',
  sendLabel = 'Transferir',
}) {
  const isAssign = mode === 'assign'
  const [peerId, setPeerId] = useState(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState([])
  const [loadingMsg, setLoadingMsg] = useState(false)
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [justSent, setJustSent] = useState(false)
  const bottomRef = useRef(null)
  const amountRef = useRef(null)

  const selected = useMemo(
    () => peers.find((p) => p.id === peerId) ?? null,
    [peers, peerId],
  )

  const filteredPeers = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return peers
    return peers.filter((p) => {
      const hay = `${p.name || ''} ${p.email || ''} ${roleLabel(p.role)}`.toLowerCase()
      return hay.includes(q)
    })
  }, [peers, query])

  useEffect(() => {
    if (!peerId && peers.length > 0) {
      setPeerId(peers[0].id)
    }
  }, [peers, peerId])

  useEffect(() => {
    if (peerId && !peers.some((p) => p.id === peerId) && peers.length > 0) {
      setPeerId(peers[0].id)
    }
  }, [peers, peerId])

  const loadThread = useCallback(async () => {
    if (!companyId || !peerId) {
      setMessages([])
      return
    }
    setLoadingMsg(true)
    setError(null)
    try {
      if (isAssign) {
        const rows = await fetchCreditLedger(companyId, 120)
        const mapped = (Array.isArray(rows) ? rows : [])
          .filter(
            (r) =>
              Number(r.user_id) === Number(peerId) &&
              String(r.kind || '').includes('allocate'),
          )
          .map((r) => ({
            id: r.id,
            direction: 'out',
            amount: r.amount,
            created_at: r.created_at,
          }))
          .reverse()
        setMessages(mapped)
      } else {
        const rows = await fetchCreditPeerTransfers(companyId, peerId)
        setMessages(Array.isArray(rows) ? rows : [])
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setMessages([])
    } finally {
      setLoadingMsg(false)
    }
  }, [companyId, peerId, isAssign])

  useEffect(() => {
    if (!open) return
    void loadThread()
  }, [open, loadThread])

  useEffect(() => {
    if (!open) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, open])

  async function handleSend(ev) {
    ev.preventDefault()
    if (!companyId || !peerId) return
    if (!isAssign && !myUserId) return
    const n = Number(amount)
    if (!Number.isFinite(n) || n <= 0) {
      setError('Ingresá un monto válido (> 0).')
      return
    }
    if (n > myAvailable) {
      setError(`Saldo insuficiente. Disponible: ${formatContactCredits(myAvailable)}.`)
      return
    }
    setBusy(true)
    setError(null)
    setJustSent(false)
    try {
      if (isAssign) {
        await assignSellerCredits(companyId, peerId, n)
      } else {
        await transferSellerCredits(companyId, myUserId, peerId, n)
      }
      setAmount('')
      setJustSent(true)
      notifyCreditsChanged()
      await loadThread()
      if (typeof onTransferred === 'function') await onTransferred()
      window.setTimeout(() => setJustSent(false), 2500)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!peers.length) {
    return (
      <div className="rounded-2xl border border-dashed border-nx-border bg-white px-5 py-10 text-center">
        <p className="text-sm font-semibold text-nx-ink">{emptyPeersTitle}</p>
        <p className="mt-1 text-sm text-nx-ink">{emptyPeersHint}</p>
      </div>
    )
  }

  return (
    <section className="nx-fold-panel">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="credits-transfer-panel"
        onClick={() => setOpen((v) => !v)}
        className="nx-fold-header flex w-full flex-wrap items-center justify-between gap-3 px-4 py-2.5 text-left transition sm:px-5"
      >
        <div className="min-w-0 flex-1">
          <h3 className="nx-fold-title text-sm font-semibold leading-snug">{title}</h3>
          <p className="nx-fold-subtitle mt-0.5 text-xs leading-snug">{description}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="nx-fold-badge rounded-lg px-3 py-1.5 text-right">
            <p className="text-[10px] font-semibold uppercase tracking-wide">{balanceLabel}</p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums">
              {formatContactCredits(myAvailable)}
            </p>
          </div>
          <span
            className={[
              'nx-fold-chevron inline-flex size-7 items-center justify-center rounded-full border transition-transform',
              open ? 'rotate-180' : '',
            ].join(' ')}
            aria-hidden
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path
                d="M19.5 8.25l-7.5 7.5-7.5-7.5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        </div>
      </button>

      {open ? (
        <div
          id="credits-transfer-panel"
          className="nx-fold-body flex min-h-[380px] flex-col lg:min-h-[440px] lg:flex-row"
        >
          <aside className="flex max-h-52 shrink-0 flex-col border-b border-nx-border bg-[#FAFAF8] lg:max-h-none lg:w-[17.5rem] lg:border-b-0 lg:border-r">
            <div className="border-b border-nx-border px-3 py-2.5">
              <label className="sr-only" htmlFor="credit-peer-search">
                Buscar
              </label>
              <input
                id="credit-peer-search"
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Buscar por nombre o email…"
                className="nx-input w-full border-nx-border bg-white text-xs"
              />
            </div>
            <ul className="flex-1 overflow-y-auto p-2" role="listbox" aria-label="Destinatarios">
              {filteredPeers.length === 0 ? (
                <li className="px-2 py-6 text-center text-xs text-nx-ink/70">Sin resultados</li>
              ) : null}
              {filteredPeers.map((p) => {
                const active = p.id === peerId
                return (
                  <li key={p.id} role="option" aria-selected={active}>
                    <button
                      type="button"
                      onClick={() => {
                        setPeerId(p.id)
                        setError(null)
                        window.setTimeout(() => amountRef.current?.focus(), 50)
                      }}
                      className={[
                        'flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 text-left transition',
                        active
                          ? 'bg-white shadow-sm ring-1 ring-nx-brand/25'
                          : 'hover:bg-white/80',
                      ].join(' ')}
                    >
                      <span
                        className={[
                          'flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold',
                          active
                            ? 'bg-nx-brand text-white'
                            : 'bg-white text-nx-ink ring-1 ring-nx-border',
                        ].join(' ')}
                      >
                        {initials(p.name, p.email)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-nx-ink">
                          {p.name || p.email}
                        </span>
                        <span className="mt-0.5 block truncate text-[11px] text-nx-ink/70">
                          {p.email || '—'}
                        </span>
                        {roleLabel(p.role) ? (
                          <span className="mt-1 inline-block rounded-md bg-nx-card-muted px-1.5 py-0.5 text-[10px] font-medium text-nx-ink">
                            {roleLabel(p.role)}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </aside>

          <div className="flex min-w-0 flex-1 flex-col bg-[#ECE5DD]">
            <div className="flex items-center gap-3 border-b border-nx-border/60 bg-[#F0EBE3] px-5 py-3.5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-nx-brand/10 text-xs font-semibold text-nx-brand ring-1 ring-nx-brand/20">
                {initials(selected?.name, selected?.email)}
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-nx-ink">
                  {selected?.name || selected?.email || 'Destinatario'}
                </p>
                <p className="truncate text-sm text-nx-ink">
                  {[selected?.email, roleLabel(selected?.role)].filter(Boolean).join(' · ')}
                </p>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5">
              {loadingMsg ? (
                <p className="py-10 text-center text-xs text-nx-ink/70">Cargando historial…</p>
              ) : null}

              {!loadingMsg && messages.length === 0 ? (
                <div className="mx-auto flex min-h-[160px] max-w-sm flex-col items-center justify-center rounded-2xl bg-white/70 px-4 py-8 text-center shadow-sm">
                  <p className="text-sm font-semibold text-nx-ink">Sin movimientos todavía</p>
                  <p className="mt-1 text-sm text-nx-ink">
                    {isAssign
                      ? 'Las asignaciones a este manager van a aparecer acá.'
                      : 'Las transferencias que envíes o recibas van a aparecer acá.'}
                  </p>
                </div>
              ) : null}

              {!loadingMsg && messages.length > 0 ? (
                <ul className="mx-auto max-w-lg space-y-2">
                  {messages.map((m) => {
                    const mine = m.direction === 'out' || isAssign
                    return (
                      <li
                        key={m.id}
                        className={[
                          'max-w-[85%] rounded-2xl px-3.5 py-2.5 shadow-sm',
                          mine
                            ? 'ml-auto rounded-br-md bg-[#DCF8C6]'
                            : 'mr-auto rounded-bl-md bg-white',
                        ].join(' ')}
                      >
                        <p
                          className={[
                            'text-[10px] font-semibold uppercase tracking-wide',
                            mine ? 'text-emerald-800' : 'text-nx-brand',
                          ].join(' ')}
                        >
                          {mine ? (isAssign ? 'Asignado' : 'Enviado') : 'Recibido'}
                        </p>
                        <p className="mt-0.5 text-base font-semibold tabular-nums text-nx-ink">
                          {mine && !isAssign ? '−' : '+'}
                          {formatContactCredits(m.amount)}{' '}
                          <span className="text-xs font-medium text-nx-ink/70">créditos</span>
                        </p>
                        <time
                          dateTime={m.created_at || undefined}
                          className="mt-1 block text-right text-[10px] text-nx-ink/60"
                        >
                          {formatTransferWhen(m.created_at)}
                        </time>
                      </li>
                    )
                  })}
                  <li ref={bottomRef} aria-hidden className="h-0" />
                </ul>
              ) : null}
            </div>

            <form
              onSubmit={handleSend}
              className="border-t border-nx-border/70 bg-[#F0EBE3] px-4 py-3.5 sm:px-5"
            >
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <label className="min-w-0 flex-1 text-xs font-semibold text-nx-ink">
                  Monto
                  <div className="relative mt-1.5">
                    <input
                      ref={amountRef}
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      autoComplete="off"
                      className="nx-input w-full rounded-full border-nx-border bg-white pr-20 text-sm tabular-nums"
                      placeholder="0"
                      value={amount}
                      disabled={busy || disabled || !peerId}
                      onChange={(e) => setAmount(e.target.value.replace(/\D/g, ''))}
                      onKeyDown={(e) => {
                        if (['e', 'E', '+', '-', '.', ','].includes(e.key)) e.preventDefault()
                      }}
                    />
                    <span className="pointer-events-none absolute inset-y-0 right-4 flex items-center text-xs text-nx-ink/60">
                      créditos
                    </span>
                  </div>
                </label>
                <button
                  type="submit"
                  disabled={busy || disabled || !peerId || myAvailable <= 0 || !amount}
                  className="nx-btn nx-btn-primary h-[42px] shrink-0 rounded-full px-6 sm:min-w-[9.5rem]"
                >
                  {busy ? 'Enviando…' : sendLabel}
                </button>
              </div>
              {justSent ? (
                <p className="mt-2 text-xs font-medium text-emerald-800">
                  {isAssign ? 'Asignación realizada.' : 'Transferencia realizada.'}
                </p>
              ) : null}
              {error ? <p className="mt-2 text-xs font-medium text-red-700">{error}</p> : null}
              {myAvailable <= 0 ? (
                <p className="mt-2 text-sm text-nx-ink">
                  No hay saldo disponible para {isAssign ? 'asignar' : 'transferir'} en este momento.
                </p>
              ) : null}
            </form>
          </div>
        </div>
      ) : null}
    </section>
  )
}
