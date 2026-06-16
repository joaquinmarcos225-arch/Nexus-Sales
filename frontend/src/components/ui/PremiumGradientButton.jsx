/**
 * Botón premium (mismo lenguaje visual que «Enviar mensaje» en LinkedIn).
 */
export function PremiumGradientButton({
  children,
  className = '',
  disabled,
  type = 'button',
  onClick,
  fullWidth = false,
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={[
        'rounded-xl bg-gradient-to-r from-red-700 via-rose-900 to-zinc-950 py-2.5 text-sm font-bold text-white',
        'shadow-lg shadow-rose-950/30 transition',
        'hover:from-red-600 hover:via-rose-800 hover:to-black',
        'disabled:cursor-not-allowed disabled:opacity-40',
        fullWidth ? 'w-full' : '',
        className,
      ].join(' ')}
    >
      {children}
    </button>
  )
}
