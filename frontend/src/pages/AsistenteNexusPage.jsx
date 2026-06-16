import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { PageHeader } from '../layout/PageHeader'
import { assistantChat } from '../utils/api.js'
import {
  clearAssistantThread,
  loadAssistantThread,
  saveAssistantThread,
} from '../utils/assistantStorage.js'

/** Máximo alineado con backend (AssistantChatRequest). */
const MAX_TURNS = 40

export default function AsistenteNexusPage() {
  const { companyId, loading: ctxLoading } = useCompany()
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const endRef = useRef(null)
  const skipNextSave = useRef(false)

  useLayoutEffect(() => {
    if (!companyId) {
      setMessages([])
      return
    }
    const { messages: stored } = loadAssistantThread(companyId)
    setMessages(stored)
    skipNextSave.current = true
  }, [companyId])

  useEffect(() => {
    if (!companyId) {
      return
    }
    if (skipNextSave.current) {
      skipNextSave.current = false
      return
    }
    saveAssistantThread(companyId, messages)
  }, [companyId, messages])

  const scrollBottom = useCallback(() => {
    requestAnimationFrame(() => {
      endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    })
  }, [])

  useEffect(() => {
    scrollBottom()
  }, [messages, scrollBottom])

  async function send() {
    const text = draft.trim()
    if (!companyId || !text || busy) {
      return
    }
    setDraft('')
    setError(null)

    const asTurns = (arr) =>
      arr
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, content: String(m.content).trim() }))
        .filter((m) => m.content.length > 0)

    const pending = [...messages, { role: 'user', content: text }]
    setMessages(asTurns(pending))
    const payloadMsgs = asTurns(pending).slice(-MAX_TURNS)
    setBusy(true)
    try {
      const res = await assistantChat({
        company_id: companyId,
        messages: payloadMsgs,
      })
      const reply = typeof res?.reply === 'string' ? res.reply : ''
      if (!reply) {
        throw new Error('Respuesta vacía del asistente.')
      }
      setMessages(asTurns([...pending, { role: 'assistant', content: reply }]))
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setMessages(asTurns(pending.slice(0, -1)))
      setDraft(text)
    } finally {
      setBusy(false)
    }
  }

  const bubble = (role, text) =>
    ({
      outer: ['flex gap-3', role === 'user' ? 'justify-end' : 'justify-start'].join(' '),
      inner: [
        'max-w-[90%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm whitespace-pre-wrap',
        role === 'user'
          ? 'bg-nx-brand text-white'
          : 'border border-slate-200 bg-white text-slate-800',
      ].join(' '),
    })

  function clearConversation() {
    if (companyId) {
      clearAssistantThread(companyId)
    }
    setMessages([])
    setError(null)
    setDraft('')
  }

  return (
    <>
      <PageHeader
        title="Asistente Nexus"
        description="Preguntás sobre tus campañas y prospectos; Nexus arma un resumen con datos reales y responde con OpenAI."
      />

      {!companyId && !ctxLoading ? (
        <p className="mb-4 rounded-xl border border-dashed border-[#e5e7eb] bg-white px-4 py-6 text-center text-sm text-[#6b7280] shadow-sm">
          Sin empresa seleccionada (revisá el backend y `/companies`).
        </p>
      ) : null}

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      <div className="flex h-[min(72vh,640px)] min-h-[320px] flex-col rounded-2xl border border-nx-border bg-nx-card-muted/80 shadow-sm overflow-hidden">
        <div className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
          <p className="text-xs text-slate-500">
            Contexto: campañas recientes · estados · conteos por campaña · instrucciones activas aplican
            al tono cuando corresponda.
          </p>
          <button
            type="button"
            disabled={busy || messages.length === 0}
            onClick={clearConversation}
            className="rounded-lg border border-nx-border bg-white px-2 py-1 text-[11px] font-semibold text-nx-muted hover:bg-nx-bg disabled:opacity-40"
          >
            Limpiar chat
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 md:p-5">
          {ctxLoading ? (
            <p className="text-sm text-slate-500">Cargando contexto…</p>
          ) : null}

          {!busy && messages.length === 0 && companyId ? (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white/70 px-4 py-8 text-center text-sm text-slate-600">
              Probá algo como «¿Cuál campaña tiene más interesados?» o «¿Cuántos prospectos respondieron
              en total?».
            </div>
          ) : null}

          {messages.map((m, i) => (
            <div key={`${m.role}-${i}`} className={bubble(m.role).outer}>
              <div className={bubble(m.role).inner}>
                <div
                  className={
                    m.role === 'user'
                      ? 'mb-1 text-[10px] font-semibold uppercase tracking-wide text-white/65'
                      : 'mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400'
                  }
                >
                  {m.role === 'user' ? 'Vos' : 'Nexus'}
                </div>
                {m.content}
              </div>
            </div>
          ))}
          {busy ? (
            <div className="flex gap-3">
              <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                Nexus
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-500">
                Pensando…
              </div>
            </div>
          ) : null}
          <div ref={endRef} />
        </div>

        <div className="shrink-0 border-t border-slate-200 bg-white p-3 md:p-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <textarea
              className="min-h-[52px] grow rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-900 shadow-inner placeholder:text-slate-400 focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900/15"
              rows={2}
              placeholder={
                companyId
                  ? 'Escribí tu pregunta…'
                  : 'Seleccioná una empresa en el sistema para usar el asistente.'
              }
              value={draft}
              disabled={!companyId || busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === 'Enter' &&
                  !e.shiftKey &&
                  companyId &&
                  !busy &&
                  draft.trim().length > 0
                ) {
                  e.preventDefault()
                  void send()
                }
              }}
            />
            <button
              type="button"
              disabled={!companyId || busy || draft.trim().length === 0}
              onClick={() => void send()}
              className="shrink-0 rounded-xl bg-nx-brand px-5 py-2.5 text-sm font-semibold text-white shadow hover:bg-nx-brand-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              Enviar
            </button>
          </div>
          <p className="mt-2 text-[10px] text-slate-400">
            Enter para enviar · Shift+Enter para nueva línea · Se guarda en este navegador por empresa
            (localStorage). Histórico enviado a la IA: últimos {MAX_TURNS} mensajes.
          </p>
        </div>
      </div>
    </>
  )
}
