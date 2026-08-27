import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { PageHeader } from '../layout/PageHeader'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { fetchSupportThread, postSupportMessage } from '../utils/api.js'
import {
  enableSalesSupportPush,
  notifySalesSupportReply,
  requestSalesNotificationPermission,
} from '../utils/pwaNotifications.js'

function fmtWhen(iso) {
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

export default function SoportePage() {
  const { company } = useCompany()
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notifPerm, setNotifPerm] = useState(
    typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,
  )
  const bottomRef = useRef(null)
  const seenSupportIds = useRef(null)

  const load = useCallback(async () => {
    try {
      const data = await fetchSupportThread()
      setMessages(Array.isArray(data?.messages) ? data.messages : [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const t = window.setInterval(() => void load(), 15000)
    return () => window.clearInterval(t)
  }, [load])

  useEffect(() => {
    void enableSalesSupportPush().then((perm) => {
      if (perm) setNotifPerm(perm)
    })
  }, [])

  useEffect(() => {
    const incoming = messages.filter((m) => m.role === 'support')
    if (seenSupportIds.current == null) {
      seenSupportIds.current = new Set(incoming.map((m) => m.id))
      return
    }
    const fresh = incoming.filter((m) => !seenSupportIds.current.has(m.id))
    if (fresh.length) {
      void notifySalesSupportReply(fresh[fresh.length - 1].text)
      fresh.forEach((m) => seenSupportIds.current.add(m.id))
    }
  }, [messages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length])

  const empty = !loading && messages.length === 0

  const hint = useMemo(
    () =>
      company?.name
        ? `Conversación tuya con el equipo de Nexus. ${company.name} es tu empresa; no se mezcla con otros usuarios.`
        : 'Conversación tuya con el equipo de Nexus. No se comparte con el resto de tu empresa.',
    [company?.name],
  )

  async function handleSend(ev) {
    ev.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    setBusy(true)
    setDraft('')
    try {
      const data = await postSupportMessage(text)
      setMessages(Array.isArray(data?.messages) ? data.messages : [])
      setError(null)
    } catch (e) {
      setDraft(text)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 max-sm:h-[calc(100dvh-6.5rem)] max-sm:gap-2">
      <PageHeader
        title="Soporte"
        description="Chat tuyo con el equipo de Nexus. Cada persona de la empresa tiene su propio hilo."
      />
      {notifPerm === 'default' ? (
        <button
          type="button"
          className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-left text-xs text-amber-950"
          onClick={() => {
            void requestSalesNotificationPermission().then((p) => {
              setNotifPerm(p)
              if (p === 'granted') void enableSalesSupportPush()
            })
          }}
        >
          Activar notificaciones para cuando Nexus Support te responda.
        </button>
      ) : null}
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <section className="nx-fold-panel flex min-h-[min(32rem,70vh)] flex-1 flex-col overflow-hidden max-sm:min-h-0">
        <div className="nx-fold-header px-4 py-2.5 sm:px-5">
          <h2 className="nx-fold-title text-sm font-semibold">Soporte</h2>
          <p className="nx-fold-subtitle mt-0.5 text-xs">{hint}</p>
        </div>

        <div className="nx-fold-body flex min-h-0 flex-1 flex-col px-0 py-0">
          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4 sm:px-5">
            {loading ? <p className="text-sm text-nx-muted">Cargando…</p> : null}
            {empty ? (
              <div className="rounded-xl border border-dashed border-nx-border bg-nx-card-muted/50 px-4 py-8 text-center">
                <p className="text-sm font-medium text-nx-ink">¿En qué te ayudamos?</p>
                <p className="mt-1 text-xs text-nx-muted">
                  Contanos si algo no funciona, si hay que probar una conexión o si necesitás una mano con la
                  secuencia.
                </p>
              </div>
            ) : (
              messages.map((m) => {
                const mine = m.role === 'user'
                return (
                  <div key={m.id} className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                    <div
                      className={[
                        'max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm shadow-sm',
                        mine
                          ? 'rounded-br-md bg-nx-ink text-white'
                          : 'rounded-bl-md border border-nx-border bg-white text-nx-ink',
                      ].join(' ')}
                    >
                      {!mine ? (
                        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-nx-muted">
                          Soporte
                        </p>
                      ) : null}
                      <p className="whitespace-pre-wrap leading-relaxed">{m.text}</p>
                      <p className={`mt-1 text-[10px] ${mine ? 'text-white/60' : 'text-nx-subtle'}`}>
                        {fmtWhen(m.at)}
                      </p>
                    </div>
                  </div>
                )
              })
            )}
            <div ref={bottomRef} />
          </div>

          <form
            onSubmit={(e) => void handleSend(e)}
            className="border-t border-nx-border bg-white px-3 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-4"
          >
            <div className="flex items-end gap-2">
              <textarea
                rows={2}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Escribí tu mensaje…"
                className="min-h-[2.75rem] flex-1 resize-none rounded-xl border border-nx-border bg-white px-3 py-2 text-sm text-nx-ink shadow-sm outline-none focus:border-nx-brand/40 focus:ring-2 focus:ring-nx-brand/15"
                disabled={busy}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void handleSend(e)
                  }
                }}
              />
              <button
                type="submit"
                disabled={busy || !draft.trim()}
                className="nx-btn nx-btn-primary shrink-0 px-4 py-2.5 text-sm disabled:opacity-40"
              >
                {busy ? '…' : 'Enviar'}
              </button>
            </div>
          </form>
        </div>
      </section>
    </div>
  )
}
