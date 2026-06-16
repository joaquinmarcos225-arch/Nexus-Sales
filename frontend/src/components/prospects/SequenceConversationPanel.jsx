import { fmtDateTime } from '../../utils/ownershipUi.js'
import { commercialStateBadgeClass, commercialStateLabel } from '../../utils/commercialUi.js'
import {
  conversationStateBadgeClass,
  conversationStateLabel,
  formatConfidence,
  stripAutoReplyMarker,
} from '../../utils/conversationUi.js'

const CHANNEL_LABELS = {
  email: 'Email',
  linkedin: 'LinkedIn',
  whatsapp: 'WhatsApp',
}

const RESPONSE_CLASS_STYLES = {
  interesado: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
  no_interesado: 'bg-red-50 text-red-800 ring-red-600/20',
  pedir_mas_info: 'bg-sky-50 text-sky-800 ring-sky-600/20',
  derivar_a_otra_persona: 'bg-amber-50 text-amber-900 ring-amber-600/20',
  contactar_mas_adelante: 'bg-violet-50 text-violet-800 ring-violet-600/20',
  respuesta_automatica: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  desconocido: 'bg-slate-100 text-slate-600 ring-slate-500/20',
}

function MessageBubble({ msg }) {
  const inbound = msg.direction === 'inbound'
  const label =
    msg.sender_type === 'prospect'
      ? 'Prospecto'
      : msg.sender_type === 'ai'
        ? 'Nexus IA'
        : msg.sender_type
  const displayText = stripAutoReplyMarker(msg.message)
  const autoSent =
    !inbound &&
    msg.sender_type === 'ai' &&
    (msg.message || '').trim().startsWith('[auto-reply:')

  return (
    <div className={`flex ${inbound ? 'justify-start' : 'justify-end'}`}>
      <div
        className={`max-w-[92%] rounded-lg px-3 py-2 text-sm ${
          inbound
            ? 'border border-[#e5e7eb] bg-white text-[#111827]'
            : 'bg-nx-brand text-white'
        }`}
      >
        <p className="text-[10px] font-medium opacity-80">
          {label}
          {autoSent ? ' · autoenviado' : ''} · {CHANNEL_LABELS[msg.channel] || msg.channel} ·{' '}
          {fmtDateTime(msg.created_at)}
        </p>
        <pre className="mt-1 whitespace-pre-wrap font-sans text-xs leading-relaxed">
          {displayText}
        </pre>
      </div>
    </div>
  )
}

const CREATION_METHOD_LABELS = {
  calendar_link: 'vía link',
  auto_agendada_por_nexus: 'auto Nexus',
  manual: 'manual',
  calendar_sync: 'sync Calendar',
}

function MeetingBookingDebug({ booking }) {
  if (!booking?.meeting_id && !booking?.scheduled_for) return null
  const confirmed = Boolean(booking.calendar_created && booking.google_calendar_event_id)
  return (
    <div
      className={`rounded-lg border p-3 text-sm ${
        confirmed ? 'border-emerald-200 bg-emerald-50/60' : 'border-amber-200 bg-amber-50/60'
      }`}
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-[#374151]">
        Reunión {confirmed ? 'verificada en Calendar' : 'sin evento en Calendar'}
      </p>
      <dl className="mt-2 space-y-1 text-xs text-[#374151]">
        {booking.meeting_id ? (
          <div>
            <dt className="inline font-medium text-[#6b7280]">Meeting ID: </dt>
            <dd className="inline">{booking.meeting_id}</dd>
          </div>
        ) : null}
        {booking.scheduled_for ? (
          <div>
            <dt className="inline font-medium text-[#6b7280]">Fecha/hora: </dt>
            <dd className="inline">{fmtDateTime(booking.scheduled_for)}</dd>
          </div>
        ) : null}
        {booking.google_calendar_event_id ? (
          <div>
            <dt className="inline font-medium text-[#6b7280]">Event ID: </dt>
            <dd className="inline break-all font-mono text-[11px]">
              {booking.google_calendar_event_id}
            </dd>
          </div>
        ) : (
          <div className="text-amber-900">Sin event_id de Google Calendar</div>
        )}
        {booking.creation_method ? (
          <div>
            <dt className="inline font-medium text-[#6b7280]">Método: </dt>
            <dd className="inline">
              {CREATION_METHOD_LABELS[booking.creation_method] || booking.creation_method}
            </dd>
          </div>
        ) : null}
        {booking.google_calendar_html_link ? (
          <div>
            <a
              href={booking.google_calendar_html_link}
              target="_blank"
              rel="noreferrer"
              className="text-nx-brand underline"
            >
              Abrir invitación Calendar
            </a>
          </div>
        ) : null}
      </dl>
    </div>
  )
}

export function SequenceConversationPanel({ tracking, lastSimulation }) {
  const conversation = tracking?.conversation || []
  const meetingBooking = lastSimulation?.meeting_booking
  const responseClass = lastSimulation?.response_class || tracking?.last_response_class
  const responseLabel =
    lastSimulation?.response_class_label || tracking?.last_response_class_label
  const classificationSummary =
    lastSimulation?.classification_summary || null
  const commercialDebug = lastSimulation?.commercial_state_debug
  const agentTurn = lastSimulation?.agent_turn
  const autoSent = lastSimulation?.auto_sent ?? tracking?.last_auto_sent
  const deliveryMode = lastSimulation?.delivery_mode || tracking?.last_delivery_mode
  const confidence =
    lastSimulation?.classification_confidence ?? tracking?.last_classification_confidence
  const escalationReason =
    lastSimulation?.escalation_reason ?? tracking?.last_escalation_reason
  const conversationState =
    lastSimulation?.conversation_state || tracking?.conversation_state
  const conversationStateText =
    lastSimulation?.conversation_state_label ||
    tracking?.conversation_state_label ||
    conversationStateLabel(conversationState)

  if (
    !conversation.length &&
    !responseLabel &&
    !commercialDebug &&
    !agentTurn &&
    !meetingBooking
  ) {
    return null
  }

  const badgeClass =
    RESPONSE_CLASS_STYLES[responseClass] || RESPONSE_CLASS_STYLES.desconocido

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6b7280]">
          Conversación
        </h4>
        <div className="flex flex-wrap items-center gap-2">
          {conversationState ? (
            <span
              className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${conversationStateBadgeClass(conversationState)}`}
            >
              {conversationStateText}
            </span>
          ) : null}
          {tracking?.sequence_paused ? (
            <span className="inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-900 ring-1 ring-inset ring-amber-600/20">
              Secuencia pausada
            </span>
          ) : null}
        </div>
      </div>

      {responseLabel ? (
        <div className="rounded-lg border border-[#e5e7eb] bg-[#f8fafc] p-3 text-sm">
          <p className="text-xs text-[#6b7280]">Clasificación de la respuesta</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${badgeClass}`}
            >
              {responseLabel}
            </span>
            {classificationSummary ? (
              <span className="text-xs text-[#6b7280]">{classificationSummary}</span>
            ) : null}
            {lastSimulation?.reply_objective_label ||
            tracking?.last_reply_objective_label ? (
              <span className="inline-flex rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-800 ring-1 ring-inset ring-indigo-600/20">
                Objetivo:{' '}
                {lastSimulation?.reply_objective_label || tracking?.last_reply_objective_label}
              </span>
            ) : null}
            {confidence != null ? (
              <span className="text-xs text-[#6b7280]">
                Confianza: {formatConfidence(confidence)}
              </span>
            ) : null}
          </div>
          {tracking?.sequence_state ? (
            <p className="mt-1 text-xs text-[#6b7280]">
              Estado secuencia: {tracking.sequence_state}
              {tracking.prospect_status ? ` · Prospecto: ${tracking.prospect_status}` : ''}
            </p>
          ) : null}
        </div>
      ) : null}

      {agentTurn || deliveryMode ? (
        <div
          className={`rounded-lg border p-3 text-sm ${
            autoSent
              ? 'border-emerald-200 bg-emerald-50/70'
              : 'border-amber-200 bg-amber-50/70'
          }`}
        >
          <p className="text-xs font-semibold uppercase tracking-wide text-[#374151]">
            {autoSent ? 'Nexus respondió automáticamente' : 'Derivado a SDR'}
          </p>
          {autoSent ? (
            <p className="mt-1 text-xs text-[#374151]">
              Respuesta enviada por{' '}
              {CHANNEL_LABELS[agentTurn?.channel || lastSimulation?.suggested_channel] ||
                'el mismo canal'}{' '}
              · confianza {formatConfidence(confidence)} · modo testing
            </p>
          ) : (
            <p className="mt-1 text-xs text-amber-900">
              {escalationReason ||
                'La conversación requiere criterio humano antes de responder.'}
            </p>
          )}
        </div>
      ) : null}

      {meetingBooking ? <MeetingBookingDebug booking={meetingBooking} /> : null}

      {commercialDebug ? (
        <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50/60 p-3 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-amber-900">
            Estado comercial (simulación)
          </p>
          <dl className="mt-2 space-y-1 text-xs text-[#374151]">
            <div>
              <dt className="inline font-medium text-[#6b7280]">Texto: </dt>
              <dd className="inline">{commercialDebug.inbound_text}</dd>
            </div>
            <div>
              <dt className="inline font-medium text-[#6b7280]">Clasificación: </dt>
              <dd className="inline">
                {commercialDebug.response_class_label} ({commercialDebug.response_class})
                · objetivo {commercialDebug.reply_objective_label}
              </dd>
            </div>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <span
                className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${commercialStateBadgeClass(commercialDebug.previous_commercial_state)}`}
              >
                {commercialDebug.previous_commercial_state_label ||
                  commercialStateLabel(commercialDebug.previous_commercial_state)}
              </span>
              <span className="text-[#6b7280]">→</span>
              <span
                className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${commercialStateBadgeClass(commercialDebug.new_commercial_state)}`}
              >
                {commercialDebug.new_commercial_state_label ||
                  commercialStateLabel(commercialDebug.new_commercial_state)}
              </span>
              {commercialDebug.is_testing ? (
                <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-amber-900">
                  TEST
                </span>
              ) : null}
            </div>
          </dl>
        </div>
      ) : null}

      {conversation.length ? (
        <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-[#e5e7eb] bg-[#f8fafc] p-3">
          {conversation.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
        </div>
      ) : (
        <p className="text-xs text-[#6b7280]">Todavía no hay mensajes en esta secuencia.</p>
      )}

    </div>
  )
}
