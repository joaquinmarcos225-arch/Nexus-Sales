import { useMemo } from 'react'
import { openExtensionStore, resolveExtensionStoreUrl } from '../../utils/extensionStore.js'
import { WhatsAppMark } from './integrationUi.jsx'

const WA = {
  dark: '#128C7E',
  ink: '#111B21',
  muted: '#667781',
  border: '#25D36655',
}

export function ExtensionInstallPanel({ detected }) {
  const storeUrl = useMemo(() => resolveExtensionStoreUrl(), [])

  return (
    <section
      className="rounded-xl border p-5 shadow-sm"
      style={{
        borderColor: WA.border,
        background: 'linear-gradient(165deg, #FFFFFF 0%, #F0FFF6 40%, #DCF8C6 100%)',
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white shadow-sm"
            style={{ border: `1px solid ${WA.border}` }}
          >
            <WhatsAppMark className="h-8 w-8" />
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: WA.dark }}>
              Extensión Chrome
            </p>
            <h2 className="mt-1 text-base font-semibold" style={{ color: WA.ink }}>
              WhatsApp Web
            </h2>
            <p className="mt-1 text-sm" style={{ color: WA.muted }}>
              {detected
                ? 'Lista para usar con tus tareas de WhatsApp en Nexus.'
                : 'Un clic te lleva a Chrome Web Store para instalarla.'}
            </p>
          </div>
        </div>
        <span
          className="rounded-full border px-2.5 py-1 text-[11px] font-semibold"
          style={
            detected
              ? { borderColor: '#A7F3D0', backgroundColor: '#ECFDF5', color: '#065F46' }
              : { borderColor: '#FDE68A', backgroundColor: '#FFFBEB', color: '#92400E' }
          }
        >
          {detected ? 'Detectada' : 'No detectada'}
        </span>
      </div>

      {storeUrl && !detected ? (
        <div className="mt-4">
          <a
            href={storeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex rounded-lg px-4 py-2.5 text-sm font-semibold text-white hover:opacity-90"
            style={{ backgroundColor: WA.dark }}
            onClick={(e) => {
              e.preventDefault()
              openExtensionStore(storeUrl)
            }}
          >
            Agregar a Chrome
          </a>
        </div>
      ) : null}

      {!storeUrl ? (
        <p className="mt-4 text-sm" style={{ color: WA.muted }}>
          Falta configurar el link de Chrome Web Store. Pedile a Nexus Support que lo active.
        </p>
      ) : null}
    </section>
  )
}
