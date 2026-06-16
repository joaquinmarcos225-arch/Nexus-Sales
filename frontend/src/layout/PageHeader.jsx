/**
 * @param {{ title: string; description: string; actions?: import('react').ReactNode }} props
 */
export function PageHeader({ title, description, actions = null }) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        <h1 className="text-2xl font-semibold tracking-tight text-[#111827]">
          {title}
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-[#6b7280]">{description}</p>
      </div>
      {actions ? <div className="shrink-0 flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  )
}
