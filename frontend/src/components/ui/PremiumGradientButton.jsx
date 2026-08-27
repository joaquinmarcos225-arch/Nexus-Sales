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
        'rounded-xl bg-nx-brand py-2.5 text-sm font-bold text-white',
        'shadow-lg shadow-red-950/25 transition',
        'hover:bg-black active:bg-black',
        'disabled:cursor-not-allowed disabled:opacity-40',
        fullWidth ? 'w-full' : '',
        className,
      ].join(' ')}
    >
      {children}
    </button>
  )
}
