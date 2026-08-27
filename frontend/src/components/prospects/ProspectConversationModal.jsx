import { useCallback, useEffect, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { fetchProspectConversationWorkspace } from '../../utils/api.js'
import { fmtDateTime } from '../../utils/ownershipUi.js'
import {
  commercialStateBadgeClass,
  commercialStateLabel,
} from '../../utils/commercialUi.js'
import {
  conversationStateBadgeClass,
  conversationStateLabel,
  formatConfidence,
} from '../../utils/conversationUi.js'

const CHANNEL_LABELS = {
  email: 'Email',
  linkedin: 'LinkedIn',
  whatsapp: 'WhatsApp',
}

function MessageBubble({ msg }) {
  const inbound = msg.direction === 'inbound'
  const label =
    msg.sender_type === 'prospect'
      ? 'Prospecto'
      : msg.sender_type === 'ai'
        ? 'Nexus'
        : msg.sender_type

  return (
    <div className={`flex ${inbound ? 'justify-start' : 'justify-end'}`}>
      <div
        className={`max-w-[88%] rounded-xl px-3 py-2 text-sm shadow-sm ${
          inbound
            ? 'border border-nx-border bg-white text-nx-ink'
            : 'bg-nx-brand text-white'
        }`}
      >
        <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">{msg.message}</pre>
        <p className={`mt-1.5 text-[10px] ${inbound ? 'text-nx-muted' : 'text-white/80'}`}>
          {label}
          {msg.is_auto_sent ? ' · autoenviado' : msg.sender_type === 'ai' ? ' · IA' : ''} ·{' '}
          {CHANNEL_LABELS[msg.channel] || msg.channel} · {fmtDateTime(msg.created_at)}
          {msg.is_testing ? ' · TEST' : ''}
        </p>
      </div>
    </div>
  )
}

export function ProspectConversationModal({ prospect, open, onClose, includeTesting = true }) {
  const [workspace, setWorkspace] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!prospect?.id) {
      return
    }
    setLoading(true)
    setError(null)
    try {
      const ws = await fetchProspectConversationWorkspace(prospect.id, { includeTesting })
      setWorkspace(ws)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setWorkspace(null)
    } finally {
      setLoading(false)
    }
  }, [prospect?.id, includeTesting])

  useEffect(() => {
    if (open && prospect?.id) {
      void load()
    }
  }, [open, prospect?.id, load])

  if (!open || !prospect) {
    return null
  }

  const messages = workspace?.messages || []
  const meetings = workspace?.meetings || []
  const turns = workspace?.turns || []

  return (
    <Modal
      title={`Conversación · ${prospect.name || prospect.company_name}`}
      onClose={onClose}
      footer={
        <button
          type="button"
          className="rounded-lg border border-nx-border bg-white px-4 py-2 text-sm font-medium text-nx-ink hover:bg-nx-card-muted"
          onClick={onClose}
        >
          Cerrar
        </button>
      }
    >
      <div className="space-y-4">
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        {loading ? <p className="text-sm text-nx-muted">Cargando conversación…</p> : null}

        {!loading && workspace ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${commercialStateBadgeClass(workspace.commercial_state)}`}
              >
                {workspace.commercial_state_label || commercialStateLabel(workspace.commercial_state)}
              </span>
              <span
                className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${conversationStateBadgeClass(workspace.conversation_state)}`}
              >
                {workspace.conversation_state_label ||
                  conversationStateLabel(workspace.conversation_state)}
              </span>
              {workspace.commercial_state_is_testing ? (
                <span className="inline-flex rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-zinc-900">
                  TEST
                </span>
              ) : null}
            </div>

            {meetings.length ? (
              <div className="rounded-lg border border-red-200 bg-red-50/60 p-3 text-sm">
                <p className="text-xs font-semibold uppercase tracking-wide text-red-900">
                  Reuniones
                </p>
                <ul className="mt-2 space-y-2">
                  {meetings.map((m) => (
                    <li key={m.id} className="text-xs text-nx-ink">
                      <span className="font-medium">{m.title}</span> ·{' '}
                      {fmtDateTime(m.scheduled_for)} ·{' '}
                      {m.calendar_confirmed ? (
                        <span className="text-red-800">Agendada en Calendar</span>
                      ) : (
                        <span className="text-zinc-800">Pendiente Calendar</span>
                      )}
                      {m.google_calendar_event_id ? (
                        <>
                          {' '}
                          ·{' '}
                          <span className="font-mono text-[10px] text-nx-muted">
                            {m.google_calendar_event_id}
                          </span>
                        </>
                      ) : null}
                      {m.creation_method ? (
                        <>
                          {' '}
                          ·{' '}
                          <span className="text-nx-muted">
                            {m.creation_method === 'calendar_link'
                              ? 'vía link'
                              : m.creation_method === 'auto_agendada_por_nexus'
                                ? 'auto Nexus'
                                : m.creation_method}
                          </span>
                        </>
                      ) : null}
                      {m.google_calendar_html_link ? (
                        <>
                          {' '}
                          ·{' '}
                          <a
                            href={m.google_calendar_html_link}
                            target="_blank"
                            rel="noreferrer"
                            className="text-nx-brand underline"
                          >
                            Ver evento
                          </a>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {messages.length ? (
              <div className="max-h-[420px] space-y-2 overflow-y-auto rounded-lg border border-nx-border bg-nx-card-muted p-3">
                {messages.map((m) => (
                  <MessageBubble key={m.id} msg={m} />
                ))}
              </div>
            ) : (
              <p className="rounded-lg border border-dashed border-nx-border px-3 py-8 text-center text-sm text-nx-muted">
                Todavía no hay mensajes en esta conversación.
              </p>
            )}

            {turns.length ? (
              <div className="rounded-lg border border-nx-border bg-white p-3 text-xs text-nx-ink">
                <p className="font-semibold uppercase tracking-wide text-nx-muted">
                  Historial de clasificación
                </p>
                <ul className="mt-2 space-y-2">
                  {turns.map((t, idx) => (
                    <li key={`${t.day}-${idx}`} className="border-t border-nx-card-muted pt-2 first:border-0 first:pt-0">
                      {t.inbound_text ? (
                        <p className="text-nx-muted">
                          Prospecto: <span className="text-nx-ink">{t.inbound_text}</span>
                        </p>
                      ) : null}
                      <p className="mt-0.5">
                        {t.response_class_label || t.response_class}
                        {t.reply_objective_label ? ` · ${t.reply_objective_label}` : ''}
                        {t.classification_confidence != null
                          ? ` · confianza ${formatConfidence(t.classification_confidence)}`
                          : ''}
                        {t.auto_sent ? ' · Nexus autoenvió' : ''}
                        {t.escalation_reason ? ` · Derivado: ${t.escalation_reason}` : ''}
                      </p>
                      {t.meeting_scheduled_for ? (
                        <p className="text-nx-muted">
                          Reunión propuesta: {fmtDateTime(t.meeting_scheduled_for)}
                          {t.calendar_confirmed
                            ? ' · verificada en Calendar'
                            : ' · sin evento Calendar'}
                          {t.creation_method
                            ? ` · ${t.creation_method === 'auto_agendada_por_nexus' ? 'auto Nexus' : t.creation_method}`
                            : ''}
                        </p>
                      ) : null}
                      {t.google_calendar_event_id ? (
                        <p className="font-mono text-[10px] text-nx-muted">
                          Event ID: {t.google_calendar_event_id}
                        </p>
                      ) : null}
                      {t.google_calendar_html_link ? (
                        <a
                          href={t.google_calendar_html_link}
                          target="_blank"
                          rel="noreferrer"
                          className="text-nx-brand underline"
                        >
                          Link Calendar
                        </a>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </Modal>
  )
}
