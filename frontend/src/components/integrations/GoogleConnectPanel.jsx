import { CalendarMark, GmailMark } from './integrationUi.jsx'

const GO = {
  dark: '#1A73E8',
  ink: '#202124',
  muted: '#5F6368',
  border: '#DADCE0',
}

function LogoSlot({ children }) {
  return (
    <div
      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white shadow-sm"
      style={{ border: `1px solid ${GO.border}` }}
    >
      {children}
    </div>
  )
}

export function GoogleConnectPanel({
  connected,
  needsReconnect,
  account,
  busy,
  verifying,
  onConnect,
  onDisconnect,
}) {
  const pill = connected
    ? { borderColor: '#A7F3D0', backgroundColor: '#ECFDF5', color: '#065F46', label: 'Conectado' }
    : needsReconnect
      ? { borderColor: '#FDE68A', backgroundColor: '#FFFBEB', color: '#92400E', label: 'Reconectar' }
      : { borderColor: '#FDE68A', backgroundColor: '#FFFBEB', color: '#92400E', label: 'No conectado' }

  return (
    <section
      className="rounded-xl border p-5 shadow-sm"
      style={{
        borderColor: GO.border,
        background: 'linear-gradient(165deg, #FFFFFF 0%, #F8FBFF 55%, #EEF4FF 100%)',
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex items-center -space-x-1">
            <LogoSlot>
              <GmailMark className="h-8 w-8" />
            </LogoSlot>
            <LogoSlot>
              <CalendarMark className="h-8 w-8" />
            </LogoSlot>
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: GO.dark }}>
              Cuenta Google
            </p>
            <h2 className="mt-1 text-base font-semibold" style={{ color: GO.ink }}>
              Gmail y Calendar
            </h2>
          </div>
        </div>
        <span
          className="rounded-full border px-2.5 py-1 text-[11px] font-semibold"
          style={{
            borderColor: pill.borderColor,
            backgroundColor: pill.backgroundColor,
            color: pill.color,
          }}
        >
          {verifying ? 'Verificando…' : pill.label}
        </span>
      </div>

      {account ? (
        <p className="mt-3 text-sm font-medium" style={{ color: GO.ink }}>
          {account}
        </p>
      ) : (
        <p className="mt-3 text-sm" style={{ color: GO.muted }}>
          Un consentimiento alcanza para correo y agenda.
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {connected && !needsReconnect ? (
          <button
            type="button"
            disabled={busy || verifying}
            onClick={() => void onDisconnect()}
            className="rounded-lg border bg-white px-4 py-2.5 text-sm font-semibold hover:bg-slate-50 disabled:opacity-50"
            style={{ borderColor: GO.border, color: GO.ink }}
          >
            {busy ? 'Desconectando…' : 'Desconectar'}
          </button>
        ) : (
          <button
            type="button"
            disabled={busy || verifying}
            onClick={() => void onConnect()}
            className="rounded-lg px-4 py-2.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
            style={{ backgroundColor: GO.dark }}
          >
            {busy ? 'Abriendo Google…' : needsReconnect ? 'Reconectar' : 'Conectar'}
          </button>
        )}
      </div>
    </section>
  )
}
