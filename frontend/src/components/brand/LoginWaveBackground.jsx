/**
 * Fondo de login: degradé rojo/negro estático (sin líneas ni nodos animados).
 */
export function LoginWaveBackground({ className = '' }) {
  return (
    <div
      className={['nx-login-wave-bg absolute inset-0 overflow-hidden', className]
        .filter(Boolean)
        .join(' ')}
      aria-hidden
      style={{
        background: `
          radial-gradient(ellipse 70% 55% at 50% 32%, rgba(220,38,38,0.34) 0%, rgba(127,29,29,0.14) 42%, transparent 72%),
          radial-gradient(ellipse 45% 40% at 12% 78%, rgba(185,28,28,0.16) 0%, transparent 70%),
          linear-gradient(165deg, #1a0a0a 0%, #0c0606 48%, #080404 100%)
        `,
      }}
    />
  )
}
