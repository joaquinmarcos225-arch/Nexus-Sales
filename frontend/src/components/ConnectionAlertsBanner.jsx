import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'

/** Avisos arriba de todo: canal desconectado (Gmail / Calendar / WhatsApp) o pago del plan. */
export function ConnectionAlertsBanner() {
  const { user } = useAuth()
  const alerts = user?.connection_alerts || []
  if (!alerts.length) return null

  return (
    <div className="relative z-10 space-y-px">
      {alerts.map((a) => {
        const warn = a.tone === 'warn'
        return (
          <div
            key={a.key}
            role="alert"
            className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b px-4 py-2 sm:px-6 ${
              warn
                ? 'border-amber-200 bg-amber-50 text-amber-950'
                : 'border-rose-200 bg-rose-50 text-rose-950'
            }`}
          >
            <p className="min-w-0 text-[13px] leading-snug">
              <span className="font-semibold">{a.title}.</span>{' '}
              <span className="opacity-90">{a.detail}</span>
            </p>
            {a.action_url ? (
              <Link
                to={a.action_url}
                className={`shrink-0 rounded-lg px-3 py-1 text-xs font-semibold text-white ${
                  warn ? 'bg-amber-700 hover:bg-amber-800' : 'bg-rose-600 hover:bg-rose-700'
                }`}
              >
                {a.action_label || 'Conectar'}
              </Link>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
