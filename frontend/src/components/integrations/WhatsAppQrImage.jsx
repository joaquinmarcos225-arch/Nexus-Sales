import { useEffect, useState } from 'react'
import QRCode from 'qrcode'

export function WhatsAppQrImage({ pngBase64, qrCode }) {
  const [fallbackSrc, setFallbackSrc] = useState('')
  const [fallbackError, setFallbackError] = useState(null)

  useEffect(() => {
    if (pngBase64 || !qrCode) {
      setFallbackSrc('')
      setFallbackError(null)
      return undefined
    }
    let alive = true
    setFallbackError(null)
    Promise.resolve(
      QRCode.toDataURL(String(qrCode), {
        width: 256,
        margin: 1,
        errorCorrectionLevel: 'M',
      }),
    )
      .then((url) => {
        if (alive) setFallbackSrc(url)
      })
      .catch((err) => {
        if (alive) setFallbackError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      alive = false
    }
  }, [pngBase64, qrCode])

  const src = pngBase64 ? `data:image/png;base64,${pngBase64}` : fallbackSrc
  if (!src) {
    return (
      <p className="mt-2 text-nx-muted">
        {fallbackError ? `No se pudo dibujar el QR: ${fallbackError}` : 'Generando QR…'}
      </p>
    )
  }

  return (
    <div className="mt-3 flex flex-col items-start gap-1">
      <img
        src={src}
        alt="Código QR WhatsApp"
        className="h-56 w-56 rounded border bg-white p-2"
      />
      <p className="text-[11px] text-nx-muted">
        QR de vinculación WhatsApp Web (escaneá con la app en el teléfono).
      </p>
    </div>
  )
}
