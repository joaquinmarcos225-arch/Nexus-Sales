import { Link } from 'react-router-dom'

const PILLARS = [
  {
    key: 'outreach',
    title: 'Contactar en masa',
    description: 'Secuencias · email, LinkedIn, WhatsApp y llamadas.',
    to: '/campanas',
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 01.778-.332 48.294 48.294 0 005.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z"
      />
    ),
  },
  {
    key: 'campaigns',
    title: 'Campañas activas',
    description: 'Centro operativo por campaña: cola, secuencia y Gmail.',
    to: '/campanas',
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z"
      />
    ),
  },
]

export function SdrConsolePillars() {
  return (
    <section className="nx-surface-panel rounded-2xl p-5 lg:p-6" data-tour="sdr-pillars">
      <div className="max-w-2xl">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-nx-brand">Nexus automatiza</p>
        <h2 className="mt-1 text-lg font-semibold text-nx-ink">Consola</h2>
        <p className="mt-2 text-sm leading-relaxed text-nx-muted">
          Elegí el paso del día: contactar, agendar o entrar a una campaña. Al iniciar una campaña
          Nexus busca los leads por vos; acá ves colas, secuencias y borradores.
        </p>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {PILLARS.map((p) => (
          <Link key={p.key} to={p.to} className="nx-pillar-card group flex flex-col p-4">
            <span className="flex size-9 items-center justify-center rounded-lg border border-nx-border bg-nx-brand-soft text-nx-brand">
              <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.75">
                {p.icon}
              </svg>
            </span>
            <span className="mt-3 text-sm font-semibold text-nx-ink group-hover:text-nx-brand">{p.title}</span>
            <span className="mt-1 text-xs leading-relaxed text-nx-muted">{p.description}</span>
          </Link>
        ))}
      </div>

      <p className="mt-4 text-[11px] text-nx-muted">
        Canales personales: Gmail, LinkedIn y más →{' '}
        <Link to="/configuracion/integraciones" className="font-medium text-nx-brand hover:underline">
          Configuración
        </Link>
      </p>
    </section>
  )
}
