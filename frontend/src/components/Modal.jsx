export function Modal({ title, children, footer, onClose }) {
  return (
    <div className="fixed inset-0 z-[100] flex items-end justify-center bg-[#111827]/45 p-0 sm:items-center sm:p-4">
      <button
        type="button"
        className="absolute inset-0"
        aria-label="Cerrar"
        onClick={onClose}
      />

      <div
        role="dialog"
        aria-modal="true"
        className="relative z-[101] flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-t-2xl border border-[#e5e7eb] bg-[#f8fafc] shadow-2xl shadow-[#111827]/15 sm:rounded-2xl"
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-[#e5e7eb] bg-white/90 px-5 py-4">
          <h2 className="text-base font-semibold text-[#111827]">{title}</h2>
          <button
            type="button"
            className="rounded-lg p-1 text-[#6b7280] hover:bg-[#e5e7eb] hover:text-[#111827]"
            onClick={onClose}
            aria-label="Cerrar diálogo"
          >
            ×
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer ? (
          <div className="flex shrink-0 flex-col-reverse gap-2 border-t border-[#e5e7eb] bg-[#f8fafc] px-5 py-4 sm:flex-row sm:justify-end">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}
