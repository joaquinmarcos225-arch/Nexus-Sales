export function AlertBanner({ message, onDismiss }) {
  if (!message) {
    return null
  }
  return (
    <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <p className="flex-1">{message}</p>
      {onDismiss ? (
        <button
          type="button"
          className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-red-800 hover:bg-red-100"
          onClick={onDismiss}
        >
          Cerrar
        </button>
      ) : null}
    </div>
  )
}
