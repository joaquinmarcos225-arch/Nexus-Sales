import { useEffect, useMemo, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { isNexusWhatsAppExtensionReady } from '../../utils/whatsappAssistExtension.js'
import { openExtensionStore, resolveExtensionStoreUrl } from '../../utils/extensionStore.js'

/**
 * Asistente de vínculo LinkedIn.
 * La extensión la provisiona el equipo Nexus; el SDR solo confirma perfil + sesión.
 */
export function LinkedInConnectModal({ open, onClose, onConfirm, busy = false }) {
  const storeUrl = useMemo(() => resolveExtensionStoreUrl(), [])
  const [extensionOk, setExtensionOk] = useState(false)
  const [loggedInLi, setLoggedInLi] = useState(false)
  const [profileUrl, setProfileUrl] = useState('')
  const [localError, setLocalError] = useState(null)

  function refreshExtension() {
    setExtensionOk(isNexusWhatsAppExtensionReady())
  }

  useEffect(() => {
    if (!open) return
    refreshExtension()
    setLoggedInLi(false)
    setProfileUrl('')
    setLocalError(null)
  }, [open])

  if (!open) return null

  const profileTrim = profileUrl.trim()
  const profileLooksOk = /^https?:\/\/(www\.)?linkedin\.com\/in\/[\w%-]+\/?/i.test(profileTrim)
  const canSubmit = loggedInLi && profileLooksOk && !busy

  async function handleConfirm() {
    setLocalError(null)
    if (!profileTrim || !profileLooksOk) {
      setLocalError('Pegá la URL de tu perfil (ej. https://www.linkedin.com/in/tu-usuario).')
      return
    }
    if (!loggedInLi) {
      setLocalError('Confirmá que tenés sesión iniciada en LinkedIn en este navegador.')
      return
    }
    await onConfirm({
      profileUrl: profileTrim,
      extensionReady: extensionOk,
    })
  }

  return (
    <Modal
      title="Conectar LinkedIn"
      onClose={() => {
        if (!busy) onClose?.()
      }}
      footer={
        <>
          <button
            type="button"
            className="nx-btn nx-btn-secondary"
            disabled={busy}
            onClick={() => onClose?.()}
          >
            Cancelar
          </button>
          <button
            type="button"
            className="nx-btn nx-btn-primary"
            disabled={!canSubmit}
            onClick={() => void handleConfirm()}
          >
            {busy ? 'Conectando…' : 'Conectar LinkedIn'}
          </button>
        </>
      }
    >
      <div className="space-y-4 text-sm text-nx-ink">
        <p className="text-nx-muted">
          Vinculá tu perfil. Nexus <strong className="text-nx-ink">no pide tu contraseña</strong> de
          LinkedIn: al enviar, se abre el chat y vos confirmás con Enter.
        </p>

        <ol className="space-y-3">
          <li className="rounded-lg border border-nx-border bg-white px-3 py-3">
            <label className="block font-semibold" htmlFor="li-profile-url">
              1. URL de tu perfil LinkedIn
            </label>
            <input
              id="li-profile-url"
              type="url"
              className="nx-input mt-2 text-sm"
              placeholder="https://www.linkedin.com/in/tu-usuario"
              value={profileUrl}
              disabled={busy}
              onChange={(e) => setProfileUrl(e.target.value)}
            />
          </li>

          <li className="rounded-lg border border-nx-border bg-white px-3 py-3">
            <p className="font-semibold">2. Sesión en este navegador</p>
            <p className="mt-1 text-xs text-nx-muted">
              Abrí LinkedIn e iniciá sesión con la cuenta de ese perfil.
            </p>
            <label className="mt-3 flex cursor-pointer items-start gap-2 text-xs text-nx-ink">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={loggedInLi}
                onChange={(e) => setLoggedInLi(e.target.checked)}
              />
              <span>Ya inicié sesión en LinkedIn en este Chrome</span>
            </label>
          </li>

          <li className="rounded-lg border border-nx-border bg-white px-3 py-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="font-semibold">3. Extensión Nexus (WhatsApp)</p>
                <p className="mt-1 text-xs text-nx-muted">
                  Para WhatsApp asistido. LinkedIn en Nexus no depende de la extensión.
                </p>
                <div className="mt-2.5 flex flex-wrap gap-2">
                  {storeUrl ? (
                    <a
                      href={storeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="nx-btn nx-btn-primary px-3 py-1.5 text-xs"
                      onClick={(e) => {
                        e.preventDefault()
                        openExtensionStore(storeUrl)
                      }}
                    >
                      Agregar a Chrome
                    </a>
                  ) : null}
                  <button
                    type="button"
                    className="nx-btn nx-btn-secondary px-3 py-1.5 text-xs"
                    onClick={refreshExtension}
                  >
                    Verificar extensión
                  </button>
                </div>
              </div>
              <span
                className={[
                  'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold',
                  extensionOk ? 'bg-emerald-50 text-emerald-800' : 'bg-slate-100 text-slate-700',
                ].join(' ')}
              >
                {extensionOk ? 'Activa' : 'Pendiente'}
              </span>
            </div>
          </li>
        </ol>

        {localError ? <p className="text-xs font-medium text-red-700">{localError}</p> : null}
      </div>
    </Modal>
  )
}
