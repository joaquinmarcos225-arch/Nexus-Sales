/**
 * @param {{
 *   children: import('react').ReactNode
 *   variant?: 'primary' | 'secondary' | 'ghost'
 *   className?: string
 *   type?: 'button' | 'submit' | 'reset'
 *   disabled?: boolean
 *   onClick?: () => void
 * }} props
 */
export function Button({
  children,
  variant = 'primary',
  className = '',
  type = 'button',
  disabled = false,
  onClick,
}) {
  const variantClass =
    variant === 'secondary'
      ? 'nx-btn-secondary'
      : variant === 'ghost'
        ? 'nx-btn-ghost'
        : 'nx-btn-primary'
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={['nx-btn', variantClass, className].filter(Boolean).join(' ')}
    >
      {children}
    </button>
  )
}
