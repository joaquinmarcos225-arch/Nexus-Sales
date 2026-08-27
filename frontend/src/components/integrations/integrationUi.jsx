/** Temas visuales por proveedor (colores oficiales aproximados). */

export function crmEffectiveStatus(verifyRow) {
  if (!verifyRow?.configured && !verifyRow?.company_connected) return 'not_connected'
  if (!verifyRow?.enabled) return 'not_connected'
  if (verifyRow?.api_reachable) return 'functional'
  if (verifyRow?.company_connected || verifyRow?.configured) return 'reconnect_required'
  return 'not_connected'
}

const BRANDS = {
  google_calendar: {
    id: 'google_calendar',
    accent: '#1A73E8',
    accentSoft: '#E8F0FE',
    ink: '#202124',
    muted: '#5F6368',
    border: '#DADCE0',
    cardBg: 'linear-gradient(165deg, #FFFFFF 0%, #F8FBFF 55%, #EEF4FF 100%)',
    headerBar: 'linear-gradient(90deg, #4285F4 0%, #34A853 33%, #FBBC04 66%, #EA4335 100%)',
    panelBg: '#F1F3F4',
    statusOkColor: '#1A73E8',
    pillOkStyle: { backgroundColor: '#E8F0FE', color: '#174EA6', borderColor: '#AECBFA' },
    fontClass: 'font-sans tracking-tight',
    titleWeight: 500,
  },
  gmail: {
    id: 'gmail',
    accent: '#EA4335',
    accentSoft: '#FCE8E6',
    ink: '#202124',
    muted: '#5F6368',
    border: '#DADCE0',
    cardBg: 'linear-gradient(165deg, #FFFFFF 0%, #FFF8F7 50%, #FCE8E6 100%)',
    headerBar: 'linear-gradient(90deg, #EA4335 0%, #FBBC04 40%, #34A853 70%, #4285F4 100%)',
    panelBg: '#F1F3F4',
    statusOkColor: '#C5221F',
    pillOkStyle: { backgroundColor: '#FCE8E6', color: '#C5221F', borderColor: '#F6AEA9' },
    fontClass: 'font-sans tracking-tight',
    titleWeight: 500,
  },
  linkedin: {
    id: 'linkedin',
    accent: '#0A66C2',
    accentSoft: '#E8F3FF',
    ink: '#000000E6',
    muted: '#00000099',
    border: '#0A66C233',
    cardBg: 'linear-gradient(165deg, #FFFFFF 0%, #F3F6F8 45%, #E8F3FF 100%)',
    headerBar: 'linear-gradient(90deg, #0A66C2 0%, #004182 100%)',
    panelBg: '#F3F6F8',
    statusOkColor: '#0A66C2',
    pillOkStyle: { backgroundColor: '#E8F3FF', color: '#004182', borderColor: '#0A66C255' },
    fontClass: 'font-sans',
    titleWeight: 600,
  },
  whatsapp: {
    id: 'whatsapp',
    accent: '#25D366',
    accentSoft: '#E7F9EF',
    ink: '#111B21',
    muted: '#667781',
    border: '#25D36644',
    cardBg: 'linear-gradient(165deg, #FFFFFF 0%, #F0FFF6 40%, #DCF8C6 100%)',
    headerBar: 'linear-gradient(90deg, #25D366 0%, #128C7E 55%, #075E54 100%)',
    panelBg: '#F0F2F5',
    statusOkColor: '#075E54',
    pillOkStyle: { backgroundColor: '#E7F9EF', color: '#075E54', borderColor: '#25D36666' },
    fontClass: 'font-sans',
    titleWeight: 600,
  },
}

export function brandTheme(brand) {
  return BRANDS[brand] || BRANDS.google_calendar
}

function GoogleGMark({ className = 'h-8 w-8' }) {
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden>
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  )
}

export function GmailMark({ className = 'h-8 w-8' }) {
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden>
      <path fill="#4CAF50" d="M45 16.2 40 18.95 35 23.7V40h7c1.7 0 3-1.3 3-3V16.2z" />
      <path fill="#1E88E5" d="M3 16.2 6.6 17.9 13 23.7V40H6c-1.7 0-3-1.3-3-3V16.2z" />
      <path fill="#E53935" d="M35 11.2 24 19.45 13 11.2 12 17 13 23.7l11 8.25 11-8.25 1-6.7z" />
      <path fill="#C62828" d="M3 12.3V16.2l10 7.5V11.2L9.9 8.3C9.1 7.7 8.2 7.5 7.3 7.5 4.6 7.5 3 9.7 3 12.3z" />
      <path fill="#FBC02D" d="M45 12.3V16.2l-10 7.5V11.2l3.1-2.9c.8-.6 1.7-.8 2.6-.8 2.7 0 4.3 2.2 4.3 4.8z" />
    </svg>
  )
}

export function CalendarMark({ className = 'h-8 w-8' }) {
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden>
      <path fill="#FFFFFF" d="M5 8h38a4 4 0 0 1 4 4v30a4 4 0 0 1-4 4H5a4 4 0 0 1-4-4V12a4 4 0 0 1 4-4z" />
      <path fill="#1A73E8" d="M1 12a4 4 0 0 1 4-4h38a4 4 0 0 1 4 4v8H1v-8z" />
      <path fill="#EA4335" d="M1 12a4 4 0 0 1 4-4h10v12H1V12z" />
      <path fill="#FBBC04" d="M15 8h18v12H15z" />
      <path fill="#34A853" d="M33 8h10a4 4 0 0 1 4 4v8H33V8z" />
      <path
        fill="#1A73E8"
        d="M19.1 38.2V24.8h3.2l5.1 8.3h.1V24.8h3.1v13.4h-3.2l-5.1-8.4h-.1v8.4h-3.1z"
      />
    </svg>
  )
}

function LinkedInMark({ className = 'h-8 w-8' }) {
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden>
      <rect width="48" height="48" rx="8" fill="#0A66C2" />
      <path
        fill="#FFFFFF"
        d="M14.5 19.5h4.2V34h-4.2V19.5zm2.1-6.7c1.35 0 2.45 1.1 2.45 2.45S17.95 17.7 16.6 17.7 14.15 16.6 14.15 15.25 15.25 12.8 16.6 12.8zM22.2 19.5h4.05v2h.06c.56-1.06 1.94-2.18 4-2.18 4.28 0 5.07 2.82 5.07 6.48V34H31.2v-7.2c0-1.72-.03-3.93-2.4-3.93-2.4 0-2.77 1.87-2.77 3.8V34H22.2V19.5z"
      />
    </svg>
  )
}

export function WhatsAppMark({ className = 'h-8 w-8' }) {
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden>
      <circle cx="24" cy="24" r="24" fill="#25D366" />
      <path
        fill="#FFFFFF"
        d="M24.1 10.4c-7.4 0-13.4 6-13.4 13.4 0 2.4.6 4.6 1.7 6.6L10.4 37.6l7.4-1.9c1.9 1 4.1 1.6 6.4 1.6 7.4 0 13.4-6 13.4-13.4S31.5 10.4 24.1 10.4zm0 24.5c-2.2 0-4.2-.6-6-1.6l-.4-.2-4.4 1.2 1.2-4.3-.3-.4c-1.1-1.8-1.7-3.9-1.7-6.1 0-6.4 5.2-11.6 11.6-11.6s11.6 5.2 11.6 11.6-5.2 11.6-11.6 11.6zm6.4-8.7c-.3-.2-2-.9-2.3-1.1-.3-.1-.5-.2-.8.2s-.9 1.1-1.1 1.3c-.2.2-.4.2-.7.1-2-.8-3.3-1.8-4.6-4.1-.3-.6.3-.5.9-1.8.1-.2 0-.4 0-.5l-.8-1.9c-.2-.5-.4-.4-.8-.4h-.6c-.2 0-.5.1-.8.4-.8.8-1.2 2-.1 3.9 1.2 2.1 2.6 3.6 5.3 4.9.7.3 1.3.5 1.8.6.7.3 1.4.2 1.9.1.6-.1 2-.8 2.2-1.6.3-.8.3-1.4.2-1.6-.1-.1-.3-.2-.6-.4z"
      />
    </svg>
  )
}

const BRAND_ICONS = {
  google_calendar: CalendarMark,
  gmail: GmailMark,
  linkedin: LinkedInMark,
  whatsapp: WhatsAppMark,
}

export const EFFECTIVE_STATUS = {
  not_connected: {
    label: 'No conectado',
    pillClass: 'bg-zinc-100 text-zinc-700 ring-zinc-300/80',
  },
  dry_run: {
    label: 'Modo prueba (simulado)',
    pillClass: 'bg-zinc-100 text-zinc-900 ring-zinc-300/80',
  },
  functional: {
    label: 'Conectado y funcional',
    pillClass: null,
  },
  reconnect_required: {
    label: 'Requiere atención de CostGuard',
    pillClass: 'bg-amber-50 text-amber-900 ring-amber-200/80',
  },
  scope_missing: {
    label: 'Permisos insuficientes',
    pillClass: 'bg-amber-50 text-amber-900 ring-amber-200/80',
  },
  error: {
    label: 'Error de conexión',
    pillClass: 'bg-red-50 text-red-900 ring-red-200/80',
  },
  pending: {
    label: 'Verificando…',
    pillClass: 'bg-zinc-50 text-zinc-800 ring-zinc-200/80',
  },
  extension_not_installed: {
    label: 'Extensión no instalada',
    pillClass: 'bg-zinc-100 text-zinc-900 ring-zinc-300/80',
  },
  extension_connected: {
    label: 'Extensión activa',
    pillClass: null,
  },
  coming_soon: {
    label: 'Próximamente',
    pillClass: 'bg-zinc-100 text-zinc-800 ring-zinc-300/80',
  },
}

export function fmtDate(iso) {
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

export function statusMeta(effectiveStatus, brand) {
  const base = EFFECTIVE_STATUS[effectiveStatus] || EFFECTIVE_STATUS.error
  const theme = brandTheme(brand)
  if (
    !base.pillStyle &&
    (effectiveStatus === 'functional' || effectiveStatus === 'extension_connected')
  ) {
    return { ...base, pillClass: null, pillStyle: theme.pillOkStyle }
  }
  return base
}

export function IntegrationCard({
  brand = 'gmail',
  title,
  description,
  effectiveStatus,
  accountLabel = 'Cuenta conectada',
  account,
  lastActivity,
  children,
  footer,
}) {
  const theme = brandTheme(brand)
  const meta = statusMeta(effectiveStatus, brand)
  const Icon = BRAND_ICONS[brand] || GoogleGMark

  return (
    <article
      className={`relative flex flex-col overflow-hidden rounded-2xl border shadow-sm ${theme.fontClass}`}
      style={{
        borderColor: theme.border,
        background: theme.cardBg,
        color: theme.ink,
      }}
    >
      <div className="h-1.5 w-full" style={{ background: theme.headerBar }} aria-hidden />
      <div className="flex flex-1 flex-col p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <div
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white shadow-sm"
              style={{
                border: `1px solid ${theme.border}`,
                boxShadow: `0 1px 3px ${theme.accent}18`,
              }}
            >
              <Icon className="h-8 w-8" />
            </div>
            <div className="min-w-0">
              <h2
                className="text-[15px] tracking-tight"
                style={{ color: theme.accent, fontWeight: theme.titleWeight }}
              >
                {title}
              </h2>
              {description ? (
                <p className="mt-1 text-xs leading-relaxed" style={{ color: theme.muted }}>
                  {description}
                </p>
              ) : null}
            </div>
          </div>
          <span
            className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${meta.pillClass || 'border'}`}
            style={meta.pillStyle || undefined}
          >
            {meta.label}
          </span>
        </div>

        <dl
          className="mt-4 space-y-2 border-t pt-3 text-xs"
          style={{ borderColor: `${theme.border}`, color: theme.muted }}
        >
          <div className="flex justify-between gap-2">
            <dt>{accountLabel}</dt>
            <dd className="text-right font-medium" style={{ color: theme.ink }}>
              {account || '—'}
            </dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt>Última actividad</dt>
            <dd className="text-right" style={{ color: theme.ink }}>
              {fmtDate(lastActivity)}
            </dd>
          </div>
        </dl>

        {children
          ? typeof children === 'function'
            ? children({ brand, theme })
            : children
          : null}

        {footer ? (
          <div
            className="mt-4 flex flex-wrap items-center gap-2 border-t pt-4"
            style={{ borderColor: theme.border }}
          >
            {footer}
          </div>
        ) : null}
      </div>
    </article>
  )
}

export const BTN_PRIMARY = 'nx-btn nx-btn-primary px-4 py-2 text-xs'
export const BTN_SECONDARY =
  'rounded-lg border border-nx-border-strong bg-white px-4 py-2 text-xs font-semibold text-nx-ink shadow-sm hover:bg-nx-card-muted disabled:opacity-40'
export const BTN_OUTLINE =
  'rounded-lg border border-nx-border bg-white px-4 py-2 text-xs font-semibold text-nx-ink shadow-sm hover:bg-nx-card-muted disabled:opacity-40'
export const BTN_DANGER =
  'rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-xs font-semibold text-red-900 hover:bg-red-100 disabled:opacity-40'

export function IntegrationStatusPanel({
  brand,
  title = 'Estado',
  verifying,
  verifyingMessage = 'Comprobando…',
  notConnected,
  notConnectedMessage,
  children,
}) {
  const theme = brandTheme(brand)
  return (
    <div
      className="mt-3 rounded-xl border p-3 text-xs"
      style={{
        borderColor: theme.border,
        background: theme.panelBg,
        color: theme.muted,
      }}
    >
      <p className="font-semibold" style={{ color: theme.ink }}>
        {title}
      </p>
      {verifying ? (
        <p className="mt-1">{verifyingMessage}</p>
      ) : notConnected ? (
        <p className="mt-1">{notConnectedMessage}</p>
      ) : (
        <ul className="mt-2 space-y-1">{children}</ul>
      )}
    </div>
  )
}

export function StatusRow({ brand, label, ok, okText = 'sí', badText = 'no', suffix = '' }) {
  const theme = brandTheme(brand)
  return (
    <li>
      {label}:{' '}
      <span
        className="font-medium"
        style={{ color: ok ? theme.statusOkColor : '#27272a' }}
      >
        {ok ? okText : badText}
      </span>
      {suffix}
    </li>
  )
}
