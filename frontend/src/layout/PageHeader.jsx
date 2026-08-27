/**
 * @param {{ title: string; description?: string; actions?: import('react').ReactNode; kicker?: string }} props
 */
export function PageHeader({ title, description, actions = null, kicker = null }) {
  return (
    <header className="mb-6 border-b border-nx-border pb-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          {kicker ? <p className="nx-kicker">{kicker}</p> : null}
          <h1
            className={[
              'font-semibold tracking-tight text-nx-ink',
              kicker ? 'mt-1 text-2xl' : 'text-2xl sm:text-[1.65rem]',
            ].join(' ')}
          >
            {title}
          </h1>
          {description ? (
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-nx-ink">{description}</p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
        ) : null}
      </div>
    </header>
  )
}
